import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from typing import Any, NamedTuple

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError

from app.db.base import Database
from app.db.models import Account
from app.engine.fence import Fence
from app.engine.host.lease import Busy, LeaseManager

pytestmark = pytest.mark.db
KEY = 0x7079726F
# Двухключевая advisory-блокировка аккаунта в pg_locks: classid — первый ключ, objid — второй.
_LOCK_PIDS = text(
    "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND classid = :key AND objid = :id "
    "AND objsubid = 2 AND granted"
)


class FakeMonotonic:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Lease(NamedTuple):
    holder: str | None
    epoch: int
    expires_at: datetime | None


class Hosts:
    """Менеджеры аренды теста (свой `holder` — свой хост) и их `run()`; всё закрывается в конце
    теста."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.managers: list[LeaseManager] = []
        self.tasks: list[asyncio.Task[None]] = []

    async def open(
        self, holder: str, *, clock: Callable[[], float] = time.monotonic, **options: float
    ) -> LeaseManager:
        settings = {
            "ttl_s": 1.0,
            "margin_s": 0.3,
            "lock_timeout_s": 0.1,
            "retry_s": 0.05,
            "renew_every_s": 0.1,
        } | options
        manager = LeaseManager(self.db, holder, monotonic=clock, **settings)
        self.managers.append(manager)
        await manager.open()
        return manager

    def run(self, manager: LeaseManager) -> None:
        self.tasks.append(asyncio.create_task(manager.run()))

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for manager in self.managers:
            await manager.close()


@pytest.fixture
async def hosts(clean_db: Database) -> AsyncIterator[Hosts]:
    made = Hosts(clean_db)
    try:
        yield made
    finally:
        await made.close()


async def _lease(db: Database, account_id: int = 1) -> Lease:
    async with db.sessions() as session:
        row = (
            await session.execute(
                select(Account.lease_holder, Account.lease_epoch, Account.lease_expires_at).where(
                    Account.id == account_id
                )
            )
        ).one()
    return Lease(*row)


async def _db_now(db: Database) -> datetime:
    async with db.sessions() as session:
        now = await session.scalar(text("SELECT now()"))
    assert isinstance(now, datetime)
    return now


async def _set_epoch(db: Database, account_id: int, epoch: int) -> None:
    async with db.sessions() as session, session.begin():
        await session.execute(
            update(Account).where(Account.id == account_id).values(lease_epoch=epoch)
        )


async def _add_account(db: Database) -> int:
    async with db.sessions() as session, session.begin():
        account_id = await session.scalar(
            insert(Account).values(name="Второй").returning(Account.id)
        )
    assert account_id is not None
    return account_id


async def _lock_pids(db: Database, account_id: int = 1) -> list[int]:
    async with db.engine.connect() as conn:
        rows = await conn.execute(_LOCK_PIDS, {"key": KEY, "id": account_id})
        return [pid for (pid,) in rows]


async def _kill_lock_connection(db: Database, account_id: int = 1) -> None:
    """Обрыв соединения блокировок хоста, держащего аккаунт: `pg_terminate_backend` и ожидание
    конца его процесса."""
    (pid,) = await _lock_pids(db, account_id)
    async with db.engine.connect() as conn:
        assert await conn.scalar(text("SELECT pg_terminate_backend(:pid, 2000)"), {"pid": pid})


@contextlib.asynccontextmanager
async def _guarded(db: Database, fence: Fence) -> AsyncIterator[None]:
    """Открытая ограждённая транзакция: строка аккаунта под FOR SHARE до выхода из блока."""
    async with db.sessions() as session, session.begin():
        await fence.guard(session)
        yield


async def _wait_for(cond: Callable[[], bool], within_s: float = 3.0) -> None:
    async with asyncio.timeout(within_s):
        while True:
            if cond():
                return
            await asyncio.sleep(0.01)


def _record(manager: LeaseManager, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Исходы `renew_once` из `run()`: "ok" или SQLSTATE ошибки ("-" — без SQLSTATE)."""
    outcomes: list[str] = []
    renew_once = manager.renew_once

    async def recording() -> None:
        try:
            await renew_once()
        except DBAPIError as exc:
            outcomes.append(getattr(exc.orig, "sqlstate", None) or "-")
            raise
        outcomes.append("ok")

    monkeypatch.setattr(manager, "renew_once", recording)
    return outcomes


async def test_acquire_returns_fence_with_epoch_and_local_deadline(
    clean_db: Database, hosts: Hosts
) -> None:
    clock = FakeMonotonic()
    a = await hosts.open("A", clock=clock)
    fence = await a.acquire(1)
    assert isinstance(fence, Fence)
    assert (fence.account_id, fence.epoch) == (1, 1)
    # t снят до запроса: срок — t + ttl − margin по часам менеджера.
    assert fence.deadline == pytest.approx(100.7)
    lease = await _lease(clean_db)
    assert (lease.holder, lease.epoch) == ("A", 1)
    assert lease.expires_at is not None
    left = (lease.expires_at - await _db_now(clean_db)).total_seconds()
    assert 0.5 < left <= 1.0
    assert a.held() == [1] and a.healthy()
    # Ограда живёт по часам менеджера.
    clock.now = 100.69
    assert fence.alive
    clock.now = 100.7
    assert not fence.alive


async def test_other_host_locked_elsewhere(clean_db: Database, hosts: Hosts) -> None:
    a = await hosts.open("A")
    b = await hosts.open("B")
    assert isinstance(await a.acquire(1), Fence)
    assert await b.acquire(1) == Busy("locked_elsewhere", 30.0)
    assert b.held() == []
    lease = await _lease(clean_db)
    assert (lease.holder, lease.epoch) == ("A", 1)


async def test_lock_connection_killed_new_holder_waits_for_expiry(
    clean_db: Database, hosts: Hosts
) -> None:
    a = await hosts.open("A")
    b = await hosts.open("B")
    fence = await a.acquire(1)
    assert isinstance(fence, Fence)
    lost: list[float] = []
    fence.on_lost = lambda: lost.append(time.monotonic())
    hosts.run(a)
    await _kill_lock_connection(clean_db)
    old = await _lease(clean_db)
    assert old.expires_at is not None
    # Postgres отпустил блокировку сразу, но аренда A ещё действует.
    busy = await b.acquire(1)
    assert isinstance(busy, Busy) and busy.reason == "lease_active"
    assert 1.0 < busy.retry_in_s <= 2.0
    assert b.held() == [1]
    async with asyncio.timeout(3):
        while True:
            started = time.monotonic()
            got = await b.acquire(1)
            if isinstance(got, Fence):
                break
            assert got.reason == "lease_active"
            await asyncio.sleep(0.05)
    assert got.epoch == 2
    new = await _lease(clean_db)
    assert new.holder == "B" and new.expires_at is not None
    # Захват B — по now() базы после срока аренды A (lease_expires_at = now() + ttl).
    assert new.expires_at - timedelta(seconds=1.0) > old.expires_at
    # A остановился по местному сроку раньше, чем B начал захват.
    assert fence.deadline < started and not fence.alive
    assert lost and lost[0] < started


async def test_same_host_reacquires_own_lease_immediately(
    clean_db: Database, hosts: Hosts
) -> None:
    clock = FakeMonotonic()
    a = await hosts.open("A", clock=clock)
    first = await a.acquire(1)
    assert isinstance(first, Fence)
    await _kill_lock_connection(clean_db)
    # Обрыв замечен на продлении: блокировки забыты, соединение открывается заново.
    with pytest.raises(DBAPIError):
        await a.renew_once()
    assert a.held() == [] and not a.healthy()
    await a.open()
    first.revoke()  # аварийная остановка прежнего движка, аренда не освобождалась
    lease = await _lease(clean_db)
    assert lease.expires_at is not None and lease.expires_at > await _db_now(clean_db)
    second = await a.acquire(1)
    assert isinstance(second, Fence) and second.epoch == 2 and second.alive
    lease = await _lease(clean_db)
    assert (lease.holder, lease.epoch) == ("A", 2)
    assert a.held() == [1]


async def test_lock_not_taken_twice_on_same_connection(clean_db: Database, hosts: Hosts) -> None:
    a = await hosts.open("A")
    b = await hosts.open("B")
    first = await a.acquire(1)
    second = await a.acquire(1)
    assert isinstance(first, Fence) and isinstance(second, Fence)
    assert second.epoch == first.epoch + 1
    pids = await _lock_pids(clean_db)
    assert len(pids) == 1
    # Прежняя ограда уже не текущая: её освобождение ничего не трогает.
    await a.release(first)
    assert await _lock_pids(clean_db) == pids
    lease = await _lease(clean_db)
    assert (lease.holder, lease.epoch) == ("A", 2)
    # Одного снятия хватает — блокировка взята один раз.
    await a.release(second)
    assert await _lock_pids(clean_db) == []
    assert a.held() == []
    got = await b.acquire(1)
    assert isinstance(got, Fence) and got.epoch == 3


async def test_acquire_behind_guarded_write_is_busy(clean_db: Database, hosts: Hosts) -> None:
    a = await hosts.open("A")
    # Аренда другого хоста истекла, но его ограждённая запись ещё держит строку FOR SHARE.
    async with clean_db.sessions() as session, session.begin():
        await session.execute(
            update(Account)
            .where(Account.id == 1)
            .values(
                lease_holder="B", lease_epoch=5, lease_expires_at=text("now() - interval '1 s'")
            )
        )
    async with clean_db.sessions() as session, session.begin():
        await session.execute(text("SELECT 1 FROM accounts WHERE id = 1 FOR SHARE"))
        assert await a.acquire(1) == Busy("lease_active", 0.05)
    assert a.held() == [1]
    got = await a.acquire(1)
    assert isinstance(got, Fence) and got.epoch == 6


async def test_renew_extends_and_stale_epoch_revoked(clean_db: Database, hosts: Hosts) -> None:
    clock = FakeMonotonic()
    a = await hosts.open("A", clock=clock)
    second = await _add_account(clean_db)
    kept = await a.acquire(1)
    stale = await a.acquire(second)
    assert isinstance(kept, Fence) and isinstance(stale, Fence)
    lost: list[int] = []
    kept.on_lost = lambda: lost.append(1)
    stale.on_lost = lambda: lost.append(second)
    kept_before = await _lease(clean_db, 1)
    # Эпоха второго аккаунта в базе уже чужая.
    await _set_epoch(clean_db, second, stale.epoch + 1)
    stale_before = await _lease(clean_db, second)
    await asyncio.sleep(0.01)
    clock.now = 100.4
    await a.renew_once()
    assert kept.alive and kept.deadline == pytest.approx(101.1)
    assert not stale.alive and lost == [second]
    kept_after = await _lease(clean_db, 1)
    assert kept_after.expires_at is not None and kept_before.expires_at is not None
    assert kept_after.expires_at > kept_before.expires_at
    assert await _lease(clean_db, second) == stale_before


@pytest.mark.parametrize("executed", ["before_release", "after_reacquire"])
async def test_late_renew_response_for_old_epoch_ignored(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch, executed: str
) -> None:
    clock = FakeMonotonic()
    a = await hosts.open("A", clock=clock)
    await _set_epoch(clean_db, 1, 6)
    old = await a.acquire(1)
    assert isinstance(old, Fence) and old.epoch == 7
    renew = a._renew
    new: list[Fence] = []

    async def restarted_meanwhile(conn: Any, pairs: list[tuple[int, int]]) -> Any:
        # Продление собрано для эпохи 7; до разбора ответа аккаунт штатно остановлен и захвачен
        # снова. Запрос выполнен до освобождения (строка по эпохе 7 вернулась) или после нового
        # захвата (строки нет).
        response = await renew(conn, pairs) if executed == "before_release" else None
        await a.release(old)
        clock.now = 100.3
        fence = await a.acquire(1)
        assert isinstance(fence, Fence)
        new.append(fence)
        if response is None:
            response = await renew(conn, pairs)
        return response

    monkeypatch.setattr(a, "_renew", restarted_meanwhile)
    clock.now = 100.2
    await a.renew_once()
    (fence,) = new
    assert fence.epoch == 8 and fence.alive
    assert fence.deadline == pytest.approx(101.0)


async def test_release_clears_holder_and_prepared_renew_does_not_revive(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = await hosts.open("A")
    b = await hosts.open("B")
    fence = await a.acquire(1)
    assert isinstance(fence, Fence)
    renew = a._renew
    renewed: list[Any] = []

    async def after_release(conn: Any, pairs: list[tuple[int, int]]) -> Any:
        # Продление собрано до освобождения, выполнено после.
        await a.release(fence)
        lease = await _lease(clean_db)
        assert (lease.holder, lease.expires_at) == (None, None)
        response = await renew(conn, pairs)
        renewed.append(response[1])
        return response

    monkeypatch.setattr(a, "_renew", after_release)
    await a.renew_once()
    assert renewed == [set()]
    assert await _lease(clean_db) == Lease(None, 1, None)
    assert a.held() == []
    got = await b.acquire(1)
    assert isinstance(got, Fence) and got.epoch == 2


async def test_release_after_deadline_writes_nothing(clean_db: Database, hosts: Hosts) -> None:
    clock = FakeMonotonic()
    a = await hosts.open("A", clock=clock)
    b = await hosts.open("B")
    fence = await a.acquire(1)
    assert isinstance(fence, Fence)
    before = await _lease(clean_db)
    # Срок наступил посреди штатной остановки — она стала аварийной.
    clock.now = fence.deadline
    await a.release(fence)
    assert await _lease(clean_db) == before
    assert await _lock_pids(clean_db) == [] and a.held() == []
    busy = await b.acquire(1)
    assert isinstance(busy, Busy) and busy.reason == "lease_active"
    assert 1.0 < busy.retry_in_s <= 2.0


async def test_renew_lock_timeout_retries(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Плановое продление — раз в 10 с: до срока ограды его проводит только повтор через retry_s.
    a = await hosts.open("A", renew_every_s=10.0)
    fence = await a.acquire(1)
    assert isinstance(fence, Fence)
    deadline = fence.deadline
    before = await _lease(clean_db)
    assert before.expires_at is not None
    outcomes = _record(a, monkeypatch)
    async with _guarded(clean_db, fence):
        hosts.run(a)
        await _wait_for(lambda: len(outcomes) >= 2)
        # lock_not_available: продление упёрлось в lock_timeout и повторяется.
        assert set(outcomes) == {"55P03"}
    await _wait_for(lambda: "ok" in outcomes)
    assert fence.alive and fence.deadline > deadline
    after = await _lease(clean_db)
    assert after.expires_at is not None and after.expires_at > before.expires_at


async def test_reconnect_does_not_renew_old_epochs(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = await hosts.open("A")
    fence = await a.acquire(1)
    assert isinstance(fence, Fence)
    lost: list[float] = []
    fence.on_lost = lambda: lost.append(time.monotonic())
    outcomes = _record(a, monkeypatch)
    hosts.run(a)
    await _wait_for(lambda: "ok" in outcomes)
    killed = len(outcomes)
    await _kill_lock_connection(clean_db)
    expires = (await _lease(clean_db)).expires_at
    await _wait_for(lambda: bool(lost))
    # Обрыв замечен, соединение открыто заново и продления идут, но прежнюю эпоху не продлевают:
    # ограда истекла по своему сроку.
    after_kill = outcomes[killed:]
    assert "-" in after_kill and "ok" in after_kill[after_kill.index("-") :]
    assert a.healthy() and a.held() == []
    assert (await _lease(clean_db)).expires_at == expires
    assert not fence.alive and fence.deadline <= lost[0]
    # Аккаунт захватывается заново на новом соединении: своя аренда — сразу, и она продлевается.
    again = await a.acquire(1)
    assert isinstance(again, Fence) and again.epoch == 2 and a.held() == [1]
    deadline = again.deadline
    await _wait_for(lambda: again.deadline > deadline)


@pytest.mark.parametrize("failure", ["lock_timeout", "connection_killed"])
async def test_run_fires_on_lost_by_deadline_while_renewals_fail(
    clean_db: Database, hosts: Hosts, failure: str
) -> None:
    # lock_timeout дольше жизни ограды: продление висит, а срок наступает.
    a = await hosts.open("A", lock_timeout_s=1.0)
    fence = await a.acquire(1)
    assert isinstance(fence, Fence)
    lost: list[float] = []
    fence.on_lost = lambda: lost.append(time.monotonic())
    async with contextlib.AsyncExitStack() as stack:
        if failure == "lock_timeout":
            await stack.enter_async_context(_guarded(clean_db, fence))
        else:
            await _kill_lock_connection(clean_db)
        hosts.run(a)
        # Ограду никто не вызывает: о её сроке сообщает run().
        await _wait_for(lambda: bool(lost), within_s=2.0)
    assert fence.deadline <= lost[0] < fence.deadline + 0.2
    assert not fence.alive


async def test_run_wakes_for_fence_acquired_mid_sleep(clean_db: Database, hosts: Hosts) -> None:
    clock = FakeMonotonic()
    a = await hosts.open("A", clock=clock, renew_every_s=10.0)
    hosts.run(a)
    # Оград нет: слежение за сроками спит.
    await asyncio.sleep(0.05)
    fence = await a.acquire(1)
    assert isinstance(fence, Fence)
    lost: list[int] = []
    fence.on_lost = lambda: lost.append(1)
    # Срок новой ограды наступает раньше, чем слежение проснулось бы само (захват мог ждать
    # строку до lock_timeout после того, как снял t): его будит сам захват.
    clock.now = fence.deadline
    await _wait_for(lambda: bool(lost), within_s=0.3)
