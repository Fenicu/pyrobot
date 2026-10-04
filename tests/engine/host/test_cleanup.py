import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import Table, func, insert, select

from app.db.accounts import AccountRepo
from app.db.base import Base, Database
from app.db.models import (
    Account,
    ActionRow,
    DecisionRow,
    LedgerRow,
    MessageRow,
    MetricRow,
    MetroRunRow,
    NotificationRow,
    ScenarioRunRow,
    SettingsHistory,
    SettingsRow,
    StateSnapshot,
    TgChatMark,
    TgPeer,
    TgSession,
    UnrecognizedRow,
    User,
)
from app.engine.fence import Fence
from app.engine.host import host as host_module
from app.engine.host.lease import LeaseManager
from app.engine.transport.fake import FakeTgBackend
from app.logctx import current_account
from tests.engine.helpers import now, until
from tests.engine.host.test_host import Hosts, _add, _alive, _holder, _registered

pytestmark = pytest.mark.db
# Все таблицы с данными аккаунта — по метаданным, а не списком: новая таблица без чистки уронит
# тест.
ACCOUNT_TABLES: list[Table] = [t for t in Base.metadata.sorted_tables if "account_id" in t.c]


@pytest.fixture
async def hosts(clean_db: Database) -> AsyncIterator[Hosts]:
    made = Hosts(clean_db)
    try:
        yield made
    finally:
        await made.close()


async def _seed(db: Database, account_id: int, rows: int = 2) -> None:
    """Строки аккаунта во всех его таблицах: по `rows` строк, у таблиц с ключом по аккаунту —
    одна. Пиры и отметки сверки — с теми же id у всех аккаунтов."""
    at = now()
    async with db.sessions() as s, s.begin():
        for i in range(rows):
            msg_id = account_id * 1000 + i
            message = await s.scalar(
                insert(MessageRow)
                .values(
                    account_id=account_id,
                    chat_id=1,
                    msg_id=msg_id,
                    revision=0,
                    content_hash="h",
                    kind="new",
                    date=at,
                    received_at=at,
                    events=[],
                )
                .returning(MessageRow.id)
            )
            await s.execute(
                insert(UnrecognizedRow).values(
                    account_id=account_id,
                    message_id=message,
                    chat_id=1,
                    msg_id=msg_id,
                    first_line="?",
                )
            )
            decision = await s.scalar(
                insert(DecisionRow)
                .values(
                    account_id=account_id, at=at, kind="run", params={}, reason="r", candidates=[]
                )
                .returning(DecisionRow.id)
            )
            run = await s.scalar(
                insert(ScenarioRunRow)
                .values(
                    account_id=account_id,
                    decision_id=decision,
                    scenario="s",
                    params={},
                    started_at=at,
                    status="done",
                )
                .returning(ScenarioRunRow.id)
            )
            await s.execute(
                insert(ActionRow).values(
                    account_id=account_id,
                    source="planner",
                    kind="send",
                    chat_id=1,
                    payload={},
                    command_class="free",
                    status="confirmed",
                    scenario_run_id=run,
                )
            )
            await s.execute(
                insert(MetroRunRow).values(
                    account_id=account_id,
                    scenario_run_id=run,
                    started_at=at,
                    status="done",
                    outcome="o",
                    steps=1,
                    duration_s=1.0,
                    buffs=[],
                    grid={},
                    path=[],
                    events=[],
                    vitals=[],
                    summary={},
                )
            )
            await s.execute(
                insert(NotificationRow).values(
                    account_id=account_id, level="info", code="c", text="t"
                )
            )
            await s.execute(
                insert(MetricRow).values(account_id=account_id, ts=at, key="k", value=1.0)
            )
            await s.execute(
                insert(LedgerRow).values(
                    account_id=account_id,
                    at=at,
                    day=at.date(),
                    recorded_at=at,
                    kind="k",
                    amounts={},
                    items={},
                    chat_id=1,
                    msg_id=msg_id,
                    revision=0,
                    content_hash="h",
                    seq=0,
                )
            )
            await s.execute(
                insert(SettingsHistory).values(
                    account_id=account_id, version=i + 1, data={}, changed_by="t"
                )
            )
            await s.execute(insert(TgPeer).values(account_id=account_id, id=777 + i, type="user"))
            await s.execute(
                insert(TgChatMark).values(
                    account_id=account_id, chat_id=1 + i, from_id=0, msg_id=1
                )
            )
        await s.execute(insert(SettingsRow).values(account_id=account_id, version=1, data={}))
        await s.execute(insert(StateSnapshot).values(account_id=account_id, version=1, state={}))
    await _session(db, account_id)


async def _session(db: Database, account_id: int) -> None:
    async with db.sessions() as s, s.begin():
        await s.execute(insert(TgSession).values(account_id=account_id, dc_id=2, date=0))


async def _counts(db: Database, account_id: int) -> dict[str, int]:
    async with db.sessions() as s:
        return {
            t.name: int(
                await s.scalar(
                    select(func.count()).select_from(t).where(t.c.account_id == account_id)
                )
                or 0
            )
            for t in ACCOUNT_TABLES
        }


async def _lease(db: Database, account_id: int) -> tuple[str | None, int]:
    async with db.sessions() as s:
        row = (
            await s.execute(
                select(Account.lease_holder, Account.lease_epoch).where(Account.id == account_id)
            )
        ).one()
    return row[0], row[1]


async def _until_gone(repo: AccountRepo, *account_ids: int) -> None:
    """Пока чистка не удалит строки аккаунтов (до 5 с)."""
    async with asyncio.timeout(5.0):
        while True:
            if all([await repo.get(account_id) is None for account_id in account_ids]):
                return
            await asyncio.sleep(0.01)


async def test_cleanup_removes_every_account_table(clean_db: Database, hosts: Hosts) -> None:
    # Движков нет: строки аккаунта 1 во время теста не меняются.
    await hosts.repo.update(1, enabled=False, capacity=20)
    two = await _add(clean_db, "disabled")
    for account_id in (1, two):
        await _seed(clean_db, account_id)
    before = await _counts(clean_db, 1)
    assert len(ACCOUNT_TABLES) >= 15 and all(before.values())
    assert all((await _counts(clean_db, two)).values())
    host = await hosts.open()
    await hosts.repo.mark_deleting(two)
    host.poke()
    await _until_gone(hosts.repo, two)
    assert await _counts(clean_db, two) == dict.fromkeys(before, 0)
    assert await _counts(clean_db, 1) == before
    account = await hosts.repo.get(1)
    assert account is not None and account.status == "disabled"
    # Аренда отпущена вместе с блокировкой, чистка закончена.
    assert two not in hosts.leases[0].held() and not host._cleanups
    assert host.host_reason(two) is None


async def test_purge_in_batches_only_deleting_account(clean_db: Database) -> None:
    repo = AccountRepo(clean_db)
    two = await _add(clean_db, "disabled")
    await _seed(clean_db, two, rows=5)
    with pytest.raises(ValueError, match="not deleting"):
        await repo.purge(two, batch=2)
    assert all((await _counts(clean_db, two)).values())
    await repo.mark_deleting(two)
    await repo.purge(two, batch=2)
    assert not any((await _counts(clean_db, two)).values())
    assert await repo.get(two) is None
    # Повтор после конца ничего не делает.
    await repo.purge(two, batch=2)


async def test_purge_deletes_user_after_last_account(clean_db: Database) -> None:
    repo = AccountRepo(clean_db)
    async with clean_db.sessions() as s, s.begin():
        owner = User(login="owner", password_hash="h", role="owner")
        u = User(login="del_user", password_hash="h", role="user", deleting_at=now())
        s.add_all([owner, u])
        await s.flush()
        uid = u.id
        acc1 = Account(owner_id=uid, name="A1", status="deleting")
        acc2 = Account(owner_id=uid, name="A2", status="deleting")
        s.add_all([acc1, acc2])
        await s.flush()
        a1_id, a2_id = acc1.id, acc2.id

    # Purge acc1: acc2 still remains, so user row must remain
    await repo.purge(a1_id)
    assert await repo.get(a1_id) is None
    async with clean_db.sessions() as s:
        assert await s.get(User, uid) is not None

    # Purge acc2 (last account of deleting user): user row must be deleted
    await repo.purge(a2_id)
    assert await repo.get(a2_id) is None
    async with clean_db.sessions() as s:
        assert await s.get(User, uid) is None


async def test_cleanup_offline_logout_for_stopped_account_with_session(
    clean_db: Database, hosts: Hosts
) -> None:
    calls: list[tuple[int, str | None]] = []

    async def offline(account_id: int) -> None:
        # Выход — под арендой чистки и в контексте аккаунта.
        calls.append((account_id, await _holder(clean_db, account_id)))
        assert current_account.get() == account_id

    with_session = await _add(clean_db, "disabled")
    without = await _add(clean_db, "error")
    await _session(clean_db, with_session)
    host = await hosts.open(logout_offline=offline)
    (first,) = await _registered(host, 1)
    for account_id in (with_session, without):
        await hosts.repo.mark_deleting(account_id)
    host.poke()
    await _until_gone(hosts.repo, with_session, without)
    assert calls == [(with_session, "host-a")]
    assert host.get(1) is first and _alive(first)


async def test_offline_logout_failure_does_not_stop_cleanup(
    clean_db: Database,
    hosts: Hosts,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(host_module, "LOGOUT_TIMEOUT_S", 0.05)
    hung = await _add(clean_db, "disabled")
    broken = await _add(clean_db, "disabled")
    for account_id in (hung, broken):
        await _session(clean_db, account_id)

    async def offline(account_id: int) -> None:
        if account_id == broken:
            raise ConnectionError("telegram down")
        await asyncio.Event().wait()

    host = await hosts.open(logout_offline=offline)
    with caplog.at_level(logging.WARNING, logger=host_module.__name__):
        for account_id in (hung, broken):
            await hosts.repo.mark_deleting(account_id)
        host.poke()
        await _until_gone(hosts.repo, hung, broken)
    warned = sorted(
        r.args[0]  # type: ignore[index]
        for r in caplog.records
        if r.levelno == logging.WARNING and "not logged out" in r.getMessage()
    )
    assert warned == [hung, broken]


async def test_cleanup_waits_for_other_host_lease(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db, "disabled")
    other = LeaseManager(clean_db, "other-host", ttl_s=2.0)
    await other.open()
    try:
        fence = await other.acquire(two)
        assert isinstance(fence, Fence)
        holders: list[str | None] = []
        purge = hosts.repo.purge

        async def purging(account_id: int, **kwargs: Any) -> None:
            holders.append(await _holder(clean_db, account_id))
            await purge(account_id, **kwargs)

        hosts.repo.purge = purging  # type: ignore[method-assign]
        host = await hosts.open()
        await hosts.repo.mark_deleting(two)
        host.poke()
        # Блокировка аккаунта у другого хоста: чистка ждёт и повторяет захват.
        await until(lambda: host.host_reason(two) == "locked_elsewhere", 5.0)
        assert host.status().busy == {two: "locked_elsewhere"}
        # Другой хост упал: блокировка ушла с его соединением, а аренда действует до срока.
        await other.close()
        await until(lambda: host.host_reason(two) == "lease_active", 5.0)
        assert await hosts.repo.get(two) is not None and holders == []
        await _until_gone(hosts.repo, two)
        # Чистка — под своей арендой, захваченной после срока чужой.
        assert holders == ["host-a"]
        assert host.host_reason(two) is None and host.status().busy == {}
    finally:
        await other.close()


async def test_cleanup_on_same_host_waits_for_engine_stop(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    two = await _add(clean_db)
    # Сессия в базе есть, но выходит из Telegram сам движок: выхода без движка нет.
    await _session(clean_db, two)
    events: list[str] = []
    offline: list[int] = []
    epochs: list[tuple[str | None, int]] = []

    async def offline_logout(account_id: int) -> None:
        offline.append(account_id)

    log_out = FakeTgBackend.log_out

    async def logging_out(self: FakeTgBackend) -> None:
        events.append(f"log_out:{current_account.get()}")
        await log_out(self)

    monkeypatch.setattr(FakeTgBackend, "log_out", logging_out)
    purge = hosts.repo.purge

    async def purging(account_id: int, **kwargs: Any) -> None:
        events.append("purge")
        epochs.append(await _lease(clean_db, account_id))
        await purge(account_id, **kwargs)

    monkeypatch.setattr(hosts.repo, "purge", purging)
    host = await hosts.open(logout_offline=offline_logout)
    first, engine = await _registered(host, 1, two)
    release = asyncio.Event()
    stop = engine.stop

    async def held() -> None:
        events.append("stopping")
        await release.wait()
        await stop()
        events.append("stopped")

    engine.stop = held  # type: ignore[method-assign]
    await hosts.repo.mark_deleting(two)
    host.poke()
    await until(lambda: "stopping" in events, 5.0)
    # Удаляемый движок API уже не видит. Пока он останавливается, чистка не начинается, а
    # аренда — у него.
    assert host.get(two) is None
    host.poke()
    await asyncio.sleep(0.1)
    assert events == [f"log_out:{two}", "stopping"]
    assert await hosts.repo.get(two) is not None
    assert await _lease(clean_db, two) == ("host-a", engine.fence.epoch)
    release.set()
    await _until_gone(hosts.repo, two)
    assert events == [f"log_out:{two}", "stopping", "stopped", "purge"]
    # Чистка захватила аренду заново, после её освобождения движком.
    assert epochs == [("host-a", engine.fence.epoch + 1)]
    assert offline == []
    assert host.get(1) is first and _alive(first)
