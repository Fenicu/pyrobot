import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, insert, select, update

from app.config import AppConfig
from app.db.accounts import AccountInfo, AccountRepo, AccountStatus
from app.db.auth_repo import AuthRepo
from app.db.base import Database
from app.db.journal import DbJournal
from app.db.models import Account, MessageRow, SettingsRow
from app.db.notifications import DbNotifier
from app.db.retention import DbRetention
from app.engine.fence import Fence, LeaseLost
from app.engine.host.account import AccountRuntime, RuntimeDeps
from app.engine.host.host import EngineHost
from app.engine.host.lease import LeaseManager
from app.engine.lag import LoopLagMonitor
from app.engine.settings import RetentionSection, Settings
from app.main import Runtime
from tests.conftest import TEST_DB_URL
from tests.engine.helpers import make_msg, now, until

pytestmark = pytest.mark.db


def _config() -> AppConfig:
    return AppConfig(_env_file=None, database_url=TEST_DB_URL, transport="fake", planner=False)


class Hosts:
    """Хосты теста на одной базе (у каждого — свой менеджер аренды). Пауза между стартами не
    ждёт, а пишется в `log` вместе со стартами движков; продления нет — местный срок аренды с
    запасом на весь тест. В конце теста хосты останавливаются, соединения блокировок
    закрываются."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = AccountRepo(db)
        self.hosts: list[EngineHost] = []
        self.leases: list[LeaseManager] = []
        self.log: list[tuple[str, Any]] = []

    async def sleep(self, seconds: float) -> None:
        self.log.append(("sleep", seconds))
        await asyncio.sleep(0)

    async def make(
        self, holder: str = "host-a", *, reconcile_s: float = 30.0
    ) -> tuple[EngineHost, LeaseManager]:
        deps = RuntimeDeps(db=self.db, config=_config(), accounts=self.repo, lag=LoopLagMonitor())
        leases = LeaseManager(self.db, holder, ttl_s=300.0, busy_retry_s=0.05)
        await leases.open()
        self.leases.append(leases)
        host = EngineHost(
            deps,
            leases,
            max_engines=20,
            start_gap_s=3.0,
            reconcile_s=reconcile_s,
            sleep=self.sleep,
        )
        self.hosts.append(host)
        return host, leases

    async def open(self, holder: str = "host-a", *, reconcile_s: float = 30.0) -> EngineHost:
        host, _ = await self.make(holder, reconcile_s=reconcile_s)
        await host.start()
        return host

    async def close(self) -> None:
        for host in self.hosts:
            await host.stop()
            await host.supervisor.stop()
        for leases in self.leases:
            await leases.close()


@pytest.fixture
async def hosts(clean_db: Database, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Hosts]:
    made = Hosts(clean_db)
    start = AccountRuntime.start

    async def logged(self: AccountRuntime) -> None:
        made.log.append(("start", self.account_id))
        await start(self)

    monkeypatch.setattr(AccountRuntime, "start", logged)
    try:
        yield made
    finally:
        await made.close()


async def _add(db: Database, status: AccountStatus = "enabled") -> int:
    async with db.sessions() as session, session.begin():
        count = await session.scalar(select(func.count()).select_from(Account))
        account_id = await session.scalar(
            insert(Account).values(name=f"Аккаунт {count}", status=status).returning(Account.id)
        )
    assert account_id is not None
    return account_id


async def _holder(db: Database, account_id: int) -> str | None:
    async with db.sessions() as session:
        return await session.scalar(select(Account.lease_holder).where(Account.id == account_id))


async def _codes(db: Database, account_id: int) -> list[str]:
    return [row.code for row in await DbNotifier(db, account_id).recent()]


async def _wait_status(repo: AccountRepo, account_id: int, status: AccountStatus) -> AccountInfo:
    async with asyncio.timeout(5.0):
        while True:
            account = await repo.get(account_id)
            assert account is not None
            if account.status == status:
                return account
            await asyncio.sleep(0.01)


def _running(host: EngineHost, account_id: int) -> AccountRuntime:
    runtime = host.get(account_id)
    assert runtime is not None
    return runtime


def _alive(runtime: AccountRuntime) -> bool:
    tasks = runtime.supervisor._tasks
    return bool(tasks) and not any(t.done() for t in tasks.values())


def _spy(runtime: AccountRuntime, calls: list[str]) -> None:
    """Какая остановка досталась движку: `stop` или `abort`."""
    stop, abort = runtime.stop, runtime.abort

    async def stopped() -> None:
        calls.append("stop")
        await stop()

    async def aborted() -> None:
        calls.append("abort")
        await abort()

    runtime.stop = stopped  # type: ignore[method-assign]
    runtime.abort = aborted  # type: ignore[method-assign]


async def test_starts_enabled_accounts_in_id_order_with_gap(
    clean_db: Database, hosts: Hosts
) -> None:
    # Аккаунт без владельца при старте хоста достаётся первой учётке.
    await AuthRepo(clean_db).ensure_admin("admin", "correct horse battery")
    two = await _add(clean_db)
    await _add(clean_db, "disabled")
    await _add(clean_db, "error")
    await _add(clean_db, "deleting")
    host = await hosts.open()
    assert hosts.log == [("start", 1), ("sleep", 3.0), ("start", two)]
    assert host.status().engines == [1, two]
    assert _alive(_running(host, 1)) and _alive(_running(host, two))
    account = await hosts.repo.get(1)
    assert account is not None and account.owner_id is not None
    status = host.status()
    assert (status.holder, status.lock_connection_ok, status.busy) == ("host-a", True, {})
    assert status.tasks_ok and isinstance(status.loop_lag_ms, float)
    assert host.capacity == 20


async def test_restart_one_account_leaves_other(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    host = await hosts.open()
    first, other = _running(host, 1), _running(host, two)
    await hosts.repo.restart(1)
    host.poke()
    await until(lambda: host.get(1) not in (None, first), 5.0)
    assert _running(host, 1).generation == first.generation + 1
    assert not first.supervisor._tasks
    assert host.get(two) is other and _alive(other)
    # Перезапуск штатный: аренда освобождена и захвачена снова.
    assert _running(host, 1).fence.epoch == first.fence.epoch + 1


async def test_missed_poke_caught_by_periodic_reconcile(clean_db: Database, hosts: Hosts) -> None:
    host = await hosts.open(reconcile_s=0.1)
    first = _running(host, 1)
    await hosts.repo.restart(1)
    await until(lambda: host.get(1) not in (None, first), 5.0)
    assert _running(host, 1).generation == first.generation + 1


async def test_disable_stops_enable_starts(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    host = await hosts.open()
    first = _running(host, 1)
    calls: list[str] = []
    _spy(first, calls)
    await hosts.repo.update(1, enabled=False, capacity=20)
    host.poke()
    await until(lambda: host.get(1) is None and not first.supervisor._tasks, 5.0)
    assert calls == ["stop"]
    await until(lambda: 1 not in host._engines, 5.0)
    assert await _holder(clean_db, 1) is None
    await hosts.repo.update(1, enabled=True, capacity=20)
    host.poke()
    await until(lambda: host.get(1) is not None, 5.0)
    assert _alive(_running(host, 1))
    # Удаляемый аккаунт тоже останавливается, и сверка его не поднимает.
    other = _running(host, two)
    await hosts.repo.mark_deleting(two)
    host.poke()
    await until(lambda: two not in host._engines, 5.0)
    assert not other.supervisor._tasks and host.get(two) is None
    host.poke()
    await asyncio.sleep(0.1)
    assert host.get(two) is None and host.status().engines == [1]


async def test_crash_loop_sets_error_and_notifies(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    host = await hosts.open()
    engine, other = _running(host, 1), _running(host, two)
    calls: list[str] = []
    _spy(engine, calls)
    runs: list[int] = []

    async def boom() -> None:
        runs.append(1)
        raise RuntimeError("boom")

    # Задача движка 1 падает пять раз подряд (без пауз): супервизор движка сообщает хосту.
    engine.supervisor._base = engine.supervisor._max = 0.001
    engine.supervisor.start("boom", boom)
    await until(lambda: 1 not in host._engines, 5.0)
    assert len(runs) == 5 and host.get(1) is None
    # Аренда действовала — штатная остановка с доработкой конвейера.
    assert calls == ["stop"] and not engine.supervisor._tasks
    account = await hosts.repo.get(1)
    assert account is not None
    assert (account.status, account.status_reason) == ("error", "crash_loop:boom")
    rows = [r for r in await DbNotifier(clean_db, 1).recent() if r.code == "account_crash_loop"]
    assert len(rows) == 1 and rows[0].level == "error"
    assert await _holder(clean_db, 1) is None
    assert host.get(two) is other and _alive(other)
    host.poke()
    await asyncio.sleep(0.1)
    assert host.get(1) is None and host.host_reason(1) is None


async def test_crash_loop_during_start_is_acted_on(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = AccountRuntime._start

    async def crashing(self: AccountRuntime) -> None:
        await start(self)
        # Серия сбоев задачи, пока старт ещё идёт.
        await self.supervisor._on_crash_loop("planner")  # type: ignore[misc]

    monkeypatch.setattr(AccountRuntime, "_start", crashing)
    host, _ = await hosts.make()
    registered = asyncio.create_task(host.wait_registered(1, 0.3))
    await host.start()
    await until(lambda: 1 not in host._engines, 5.0)
    # Движок с серией сбоев так и не регистрировался.
    assert await registered is None
    account = await hosts.repo.get(1)
    assert account is not None
    assert (account.status, account.status_reason) == ("error", "crash_loop:planner")
    assert "account_crash_loop" in await _codes(clean_db, 1)
    assert await _holder(clean_db, 1) is None


async def test_start_failure_sets_error_other_accounts_unaffected(
    clean_db: Database, hosts: Hosts
) -> None:
    async with clean_db.sessions() as session, session.begin():
        session.add(SettingsRow(account_id=1, version=1, data={"engine": {"mode": "warp"}}))
    two = await _add(clean_db)
    host = await hosts.open()
    account = await hosts.repo.get(1)
    assert account is not None
    assert (account.status, account.status_reason) == ("error", "start_failed:ValidationError")
    assert host.get(1) is None and 1 not in host._engines and host.host_reason(1) is None
    assert await _holder(clean_db, 1) is None
    assert _alive(_running(host, two))
    # Настройки поправлены напрямую, аккаунт включён — движок поднимается без перезапуска хоста.
    async with clean_db.sessions() as session, session.begin():
        await session.execute(
            update(SettingsRow)
            .where(SettingsRow.account_id == 1)
            .values(data=Settings().model_dump(mode="json"))
        )
    await hosts.repo.update(1, enabled=True, capacity=20)
    host.poke()
    await until(lambda: host.get(1) is not None, 5.0)
    assert _alive(_running(host, 1)) and _alive(_running(host, two))


async def test_two_hosts_one_engine_per_account(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    a, _ = await hosts.make("host-a")
    b, _ = await hosts.make("host-b")
    await asyncio.gather(a.start(), b.start())
    for account_id in (1, two):
        owners = [host for host in (a, b) if host.get(account_id) is not None]
        assert len(owners) == 1
        other = b if owners[0] is a else a
        assert other.host_reason(account_id) == "locked_elsewhere"
        assert other.status().busy[account_id] == "locked_elsewhere"
        assert await _holder(clean_db, account_id) == owners[0].status().holder
    # Хост A остановился — B берёт его аккаунты по повтору захвата.
    await a.stop()
    await until(lambda: b.get(1) is not None and b.get(two) is not None, 5.0)
    assert b.host_reason(1) is None and b.status().busy == {}


async def test_lost_lease_aborts_and_reacquires(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    host = await hosts.open()
    first, other = _running(host, 1), _running(host, two)
    calls: list[str] = []
    _spy(first, calls)
    first.fence.revoke()
    # Останавливаемый движок API уже не видит.
    assert host.get(1) is None
    await until(lambda: host.get(1) not in (None, first), 5.0)
    assert calls == ["abort"] and not first.supervisor._tasks
    assert _running(host, 1).fence.epoch == first.fence.epoch + 1
    assert (await _codes(clean_db, 1)).count("lock_lost") == 1
    assert host.get(two) is other and _alive(other)
    account = await hosts.repo.get(1)
    assert account is not None and account.status == "enabled"


async def test_lease_lost_during_start_is_lease_loss(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = AccountRuntime._start
    tries: list[int] = []

    async def losing(self: AccountRuntime) -> None:
        tries.append(self.fence.epoch)
        if len(tries) == 1:
            self.fence.revoke()
            raise LeaseLost("lost during start")
        await start(self)

    monkeypatch.setattr(AccountRuntime, "_start", losing)
    host = await hosts.open()
    await until(lambda: host.get(1) is not None, 5.0)
    # Не сбой старта: статус не тронут, аренда захвачена заново на следующей сверке.
    account = await hosts.repo.get(1)
    assert account is not None and (account.status, account.status_reason) == ("enabled", None)
    assert tries == [1, 2] and _running(host, 1).fence.epoch == 2


async def test_restart_waits_for_previous_runtime(clean_db: Database, hosts: Hosts) -> None:
    host = await hosts.open()
    first = _running(host, 1)
    release = asyncio.Event()
    stop = first.stop
    stopping: list[bool] = []

    async def held() -> None:
        stopping.append(True)
        await release.wait()
        await stop()

    first.stop = held  # type: ignore[method-assign]
    hosts.log.clear()
    await hosts.repo.restart(1)
    host.poke()
    await until(lambda: stopping == [True], 5.0)
    host.poke()
    await asyncio.sleep(0.1)
    # Прежний движок ещё останавливается: новый не стартует, аренда у прежнего.
    assert ("start", 1) not in hosts.log and host.get(1) is None
    release.set()
    await until(lambda: host.get(1) is not None, 5.0)
    assert hosts.log.count(("start", 1)) == 1
    assert _running(host, 1).generation == first.generation + 1


async def test_wait_registered_waits_for_live_engine(clean_db: Database, hosts: Hosts) -> None:
    other = LeaseManager(clean_db, "other-host")
    await other.open()
    try:
        fence = await other.acquire(1)
        assert isinstance(fence, Fence)
        host = await hosts.open()
        assert host.get(1) is None and host.host_reason(1) == "locked_elsewhere"
        assert await host.wait_registered(1, 0.05) is None
        registered = asyncio.create_task(host.wait_registered(1, 5.0))
        await other.release(fence)
        engine = await registered
        assert engine is not None and engine is host.get(1)
        assert host.host_reason(1) is None
        # Движок с потерянной арендой не зарегистрирован: ждём новый, а не останавливаемый.
        engine.fence.revoke()
        again = await host.wait_registered(1, 5.0)
        assert again is not None and again is not engine and again is host.get(1)
    finally:
        await other.close()


async def test_host_reason_never_stale(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = LeaseManager(clean_db, "other-host")
    await other.open()
    try:
        two = await _add(clean_db)
        fence = await other.acquire(1)
        fence2 = await other.acquire(two)
        assert isinstance(fence, Fence) and isinstance(fence2, Fence)
        host = await hosts.open()
        assert (host.host_reason(1), host.host_reason(two)) == ("locked_elsewhere",) * 2
        # Аккаунт выключен — причины захвата у него больше нет.
        await hosts.repo.update(1, enabled=False, capacity=20)
        host.poke()
        await until(lambda: host.host_reason(1) is None, 5.0)
        assert host.status().busy == {two: "locked_elsewhere"}

        # Старт не удался — причины тоже нет, есть статус error.
        async def failing(self: AccountRuntime) -> None:
            raise RuntimeError("broken")

        monkeypatch.setattr(AccountRuntime, "_start", failing)
        await other.release(fence2)
        account = await _wait_status(hosts.repo, two, "error")
        assert account.status_reason == "start_failed:RuntimeError"
        assert host.host_reason(two) is None and host.status().busy == {}
        await other.release(fence)
    finally:
        await other.close()


async def test_lease_acquires_one_at_a_time(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = [await _add(clean_db, "disabled") for _ in range(3)]
    host, leases = await hosts.make()
    acquire = leases.acquire
    inflight: list[int] = []
    most = [0]

    async def counted(account_id: int) -> Any:
        inflight.append(account_id)
        most[0] = max(most[0], len(inflight))
        try:
            await asyncio.sleep(0.01)
            return await acquire(account_id)
        finally:
            inflight.remove(account_id)

    monkeypatch.setattr(leases, "acquire", counted)
    await host.start()
    for account_id in ids:
        await hosts.repo.update(account_id, enabled=True, capacity=20)
        host.poke()
    await until(lambda: all(host.get(i) is not None for i in ids), 5.0)
    _running(host, 1).fence.revoke()
    await until(lambda: host.get(1) is not None, 5.0)
    assert most[0] == 1


async def test_stop_stops_live_aborts_lost_and_releases(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    host = await hosts.open()
    live, lost = _running(host, 1), _running(host, two)
    live_calls: list[str] = []
    lost_calls: list[str] = []
    _spy(live, live_calls)
    _spy(lost, lost_calls)
    # Срок аренды наступил, а аварийная остановка ещё не прошла.
    lost.fence.on_lost = None
    lost.fence.revoke()
    await host.stop()
    assert (live_calls, lost_calls) == (["stop"], ["abort"])
    assert host.get(1) is None and host.get(two) is None and not host._engines
    # Действующая аренда освобождена, потерянная — нет (после срока хост в базу не пишет).
    assert await _holder(clean_db, 1) is None
    assert await _holder(clean_db, two) == "host-a"


async def test_process_tasks_restart_without_crash_limit(hosts: Hosts) -> None:
    host = await hosts.open()
    runs: list[int] = []

    async def flaky() -> None:
        runs.append(1)
        raise RuntimeError("boom")

    host.supervisor._base = host.supervisor._max = 0.001
    host.supervisor.start("flaky", flaky)
    # Задача процесса не бросается после серии сбоев, а перезапускается дальше.
    await until(lambda: len(runs) >= 8, 5.0)
    assert not host.supervisor._tasks["flaky"].done()
    await host.supervisor.cancel("flaky")


async def _journal(db: Database, account_id: int, *days: int) -> None:
    journal = DbJournal(db, account_id)
    for i, age in enumerate(days):
        msg = make_msg(f"m{i}", msg_id=account_id * 100 + i, received_at=now() - timedelta(age))
        await journal.append(msg, [], None, 0)


async def _messages(db: Database, account_id: int) -> int:
    async with db.sessions() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(MessageRow)
                .where(MessageRow.account_id == account_id)
            )
            or 0
        )


async def test_retention_covers_disabled_skips_deleting(clean_db: Database) -> None:
    disabled = await _add(clean_db, "disabled")
    failed = await _add(clean_db, "error")
    deleting = await _add(clean_db, "deleting")
    short = Settings(retention=RetentionSection(messages_days=7)).model_dump(mode="json")
    async with clean_db.sessions() as session, session.begin():
        session.add(SettingsRow(account_id=disabled, version=1, data=short))
    for account_id in (1, disabled, failed, deleting):
        await _journal(clean_db, account_id, 100, 10)
    runtime = Runtime(_config())
    try:
        await runtime._retention_pass()
    finally:
        await runtime.db.dispose()
    # Каждый аккаунт — по своей политике: у выключенного журнал короче (7 дней).
    assert await _messages(clean_db, 1) == 1
    assert await _messages(clean_db, failed) == 1
    assert await _messages(clean_db, disabled) == 0
    assert await _messages(clean_db, deleting) == 2


async def test_retention_failure_notifies_account_once(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    two = await _add(clean_db)
    purge = DbRetention.purge

    async def broken(self: DbRetention, *args: Any) -> dict[str, int]:
        if self._account_id == 1:
            raise ConnectionError("db down")
        return await purge(self, *args)

    monkeypatch.setattr(DbRetention, "purge", broken)
    await _journal(clean_db, two, 100)
    runtime = Runtime(_config())
    try:
        for _ in range(3):
            await runtime._retention_pass()
    finally:
        await runtime.db.dispose()
    # Сбой одного аккаунта не мешает остальным; уведомление — одно на серию.
    assert (await _codes(clean_db, 1)).count("retention_failed") == 1
    assert "retention_failed" not in await _codes(clean_db, two)
    assert await _messages(clean_db, two) == 0
