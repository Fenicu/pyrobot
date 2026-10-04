import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import func, insert, select

from app.config import AppConfig
from app.db.accounts import AccountRepo
from app.db.audit import AuditLog
from app.db.base import Database
from app.db.crypto import SecretBox
from app.db.models import (
    Account,
    MessageRow,
    SettingsHistory,
    SettingsRow,
    TgChatMark,
    TgPeer,
    TgSession,
)
from app.db.notifications import DbNotifier
from app.db.server_settings import ServerSettingsRepo
from app.db.settings_store import direct_update
from app.engine.facade import LockLostError
from app.engine.fence import Fence, LeaseLost
from app.engine.host.account import AccountRuntime, RuntimeDeps
from app.engine.host.codes import CodeLimiter
from app.engine.host.lease import LeaseManager
from app.engine.lag import LoopLagMonitor
from app.engine.settings import ChatIsSelf, Settings, SettingsPatch
from app.engine.tg_auth import TgState
from app.engine.transport.kurigram import KurigramTransport
from app.logctx import current_account
from tests.conftest import TEST_DB_URL
from tests.engine.helpers import GAME, make_msg, until
from tests.engine.kurigram_fakes import EXPECTED, FakeClient, rpc_error

pytestmark = pytest.mark.db
ACCOUNT_TASKS = {"pipeline", "gateway", "reconcile", "reactions", "team-forward", "planner"}
# Задачи движка на kurigram: ещё проба Telegram и сверка истории.
KURIGRAM_TASKS = ACCOUNT_TASKS | {"tg-probe", "history"}
BOX = SecretBox(secrets.token_bytes(32))
OTHER = -1002000000001


class Engines:
    """Движки теста на арендах одного менеджера; в конце теста всё останавливается, аренды
    освобождаются."""

    def __init__(
        self, db: Database, config: AppConfig | None = None, box: SecretBox | None = None
    ) -> None:
        config = config or AppConfig(
            _env_file=None, database_url=TEST_DB_URL, transport="fake", planner=False
        )
        self.deps = RuntimeDeps(
            db=db,
            config=config,
            accounts=AccountRepo(db),
            lag=LoopLagMonitor(),
            codes=CodeLimiter(10),
            box=box,
            server=ServerSettingsRepo(db, AuditLog(db)),
        )
        # Продление в тестах не идёт: местный срок аренды с запасом на весь тест.
        self.leases = LeaseManager(db, "test-host", ttl_s=300.0)
        self.runtimes: list[AccountRuntime] = []
        self.crash_loops: list[tuple[int, str]] = []

    async def start(self, account_id: int) -> AccountRuntime:
        fence = await self.leases.acquire(account_id)
        assert isinstance(fence, Fence)
        account = await self.deps.accounts.get(account_id)
        assert account is not None
        runtime = AccountRuntime(account, self.deps, fence, on_crash_loop=self._crash_loop)
        self.runtimes.append(runtime)
        await runtime.start()
        return runtime

    async def _crash_loop(self, account_id: int, task: str) -> None:
        self.crash_loops.append((account_id, task))

    async def close(self) -> None:
        for runtime in self.runtimes:
            await runtime.abort()
            await self.leases.release(runtime.fence)
        await self.leases.close()


@pytest.fixture
async def engines(clean_db: Database) -> AsyncIterator[Engines]:
    made = Engines(clean_db)
    await made.leases.open()
    try:
        yield made
    finally:
        await made.close()


@pytest.fixture
async def kurigram(
    clean_db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Engines]:
    """Движки на транспорте kurigram: клиент — фейк поверх настоящего `PgSessionStorage`,
    каталог данных — `tmp_path`."""
    monkeypatch.setattr(KurigramTransport, "_make_client", lambda self: FakeClient(self._storage))
    config = AppConfig(
        _env_file=None,
        database_url=TEST_DB_URL,
        transport="kurigram",
        tg_api_id=1,
        tg_api_hash=SecretStr("x"),
        data_dir=tmp_path,
        planner=False,
    )
    made = Engines(clean_db, config, BOX)
    await made.leases.open()
    try:
        yield made
    finally:
        await made.close()


async def _add_account(db: Database) -> int:
    async with db.sessions() as session, session.begin():
        account_id = await session.scalar(
            insert(Account).values(name="Второй").returning(Account.id)
        )
    assert account_id is not None
    return account_id


async def _journaled(db: Database) -> int:
    async with db.sessions() as session:
        return int(await session.scalar(select(func.count()).select_from(MessageRow)) or 0)


async def _settings_row(db: Database, account_id: int = 1) -> tuple[int, Any] | None:
    async with db.sessions() as session:
        row = await session.scalar(select(SettingsRow).where(SettingsRow.account_id == account_id))
        return None if row is None else (row.version, row.data)


async def test_two_runtimes_share_process_without_crosstalk(
    engines: Engines, clean_db: Database
) -> None:
    second = await _add_account(clean_db)
    one = await engines.start(1)
    two = await engines.start(second)
    assert one.facade is not None and two.facade is not None
    assert one.stream is not two.stream
    one_tasks = dict(one.supervisor._tasks)
    two_tasks = dict(two.supervisor._tasks)
    assert set(one_tasks) == set(two_tasks) == ACCOUNT_TASKS
    assert set(one_tasks.values()).isdisjoint(two_tasks.values())
    # Задачи создаются в контексте своего аккаунта: он попадает в каждую строку их логов.
    assert {t.get_context()[current_account] for t in one_tasks.values()} == {1}
    assert {t.get_context()[current_account] for t in two_tasks.values()} == {second}

    await one.facade.pause(by="test")
    assert one.settings.current.engine.paused is True
    assert two.settings.current.engine.paused is False
    assert {"settings", "notification"} <= {e.type for e in one.stream.history()}
    assert two.stream.history() == []

    await one.stop()
    assert all(t.done() for t in one_tasks.values())
    assert not any(t.done() for t in two_tasks.values())
    status = two.facade.status()
    assert status.workers_ok and status.lease_ok and not status.paused


async def test_stop_drains_pipeline_abort_does_not(engines: Engines, clean_db: Database) -> None:
    first = await engines.start(1)
    assert first.pipeline is not None
    for i in range(50):
        await first.pipeline.submit(make_msg(f"m{i}", msg_id=i + 1))
    await first.stop()
    assert await _journaled(clean_db) == 50
    await engines.leases.release(first.fence)

    second = await engines.start(1)
    assert second.pipeline is not None
    for i in range(50):
        await second.pipeline.submit(make_msg(f"n{i}", msg_id=100 + i))
    await second.abort()
    assert second.pipeline.backlog() > 0
    assert await _journaled(clean_db) < 100
    assert not second.supervisor._tasks


async def test_writes_after_lost_lease_refused(engines: Engines, clean_db: Database) -> None:
    runtime = await engines.start(1)
    facade = runtime.facade
    assert facade is not None
    await facade.patch_settings({"engine": {"min_request_interval_s": 2}}, version=0, by="test")
    before = await _settings_row(clean_db)
    assert before is not None and before[0] == 1

    runtime.fence.revoke()
    assert facade.status().lease_ok is False
    with pytest.raises(LeaseLost):
        await facade.patch_settings(
            {"engine": {"min_request_interval_s": 3}}, version=1, by="test"
        )
    with pytest.raises(LockLostError):
        await facade.unkill(by="test")
    assert await _settings_row(clean_db) == before
    assert runtime.settings.current.engine.min_request_interval_s == 2
    await runtime.abort()


async def _make_session_file(data_dir: Path) -> Path:
    """Файл сессии прежней установки (`SQLiteStorage` kurigram) с вошедшим пользователем."""
    from pyrogram.storage import SQLiteStorage

    source = SQLiteStorage("pyrobot", workdir=data_dir)
    await source.open()
    try:
        await source.api_id(12345)
        await source.dc_id(2)
        await source.test_mode(False)
        await source.server_address("149.154.167.51")
        await source.port(443)
        await source.auth_key(b"k" * 256)
        await source.user_id(EXPECTED)
        await source.is_bot(False)
        await source.update_peers([(GAME, 42, "bot", None), (OTHER, 99, "supergroup", None)])
        await source.save()
    finally:
        await source.close()
    return data_dir / "pyrobot.session"


async def _tg_rows(db: Database) -> tuple[TgSession | None, set[int]]:
    async with db.sessions() as session:
        row = await session.get(TgSession, 1)
        peers = await session.scalars(select(TgPeer.id).where(TgPeer.account_id == 1))
        return row, set(peers)


async def _codes(db: Database) -> list[tuple[str, str]]:
    return [(row.level, row.code) for row in await DbNotifier(db, 1).recent()]


@pytest.mark.parametrize("partial", [False, True])
async def test_imports_session_file_and_goes_online_without_login(
    kurigram: Engines, clean_db: Database, tmp_path: Path, partial: bool
) -> None:
    # Review Focus 1: перенос сессии прежней установки — аккаунт 1 онлайн без перелогина.
    path = await _make_session_file(tmp_path)
    if partial:
        # Перенос прервался до `user_id` (он пишется последним): строка есть, входа в ней нет.
        async with clean_db.sessions() as session, session.begin():
            sealed = BOX.seal(b"x" * 256, "auth_key", 1)
            session.add(TgSession(account_id=1, dc_id=4, date=0, auth_key=sealed))
    await kurigram.deps.accounts.bind_telegram(1, EXPECTED)
    runtime = await kurigram.start(1)
    assert runtime.tg is not None and runtime.tg.status().state is TgState.ONLINE
    assert runtime.tg.status().user_id == EXPECTED
    client = runtime.transport._client  # type: ignore[union-attr]
    assert isinstance(client, FakeClient) and client.is_initialized
    assert [name for name, _ in client.invoked] == ["GetState"]
    row, peers = await _tg_rows(clean_db)
    assert row is not None and row.user_id == EXPECTED and row.auth_key is not None
    assert BOX.open(row.auth_key, "auth_key", 1) == b"k" * 256
    # Пиры — только чатов из настроек.
    assert peers == {GAME}
    assert not path.exists() and (tmp_path / "pyrobot.session.migrated").exists()
    assert await _codes(clean_db) == []


async def test_broken_session_file_warns_and_keeps_file(
    kurigram: Engines, clean_db: Database, tmp_path: Path
) -> None:
    path = tmp_path / "pyrobot.session"
    path.write_bytes(b"not a database")
    runtime = await kurigram.start(1)
    assert runtime.facade is not None and runtime.tg is not None
    assert runtime.tg.status().state is TgState.UNAUTHORIZED
    assert path.read_bytes() == b"not a database"
    assert not (tmp_path / "pyrobot.session.migrated").exists()
    assert await _codes(clean_db) == [("warn", "tg_session_import_failed")]


async def test_undecryptable_session_dropped_and_reported(
    kurigram: Engines, clean_db: Database
) -> None:
    foreign = SecretBox(secrets.token_bytes(32)).seal(b"k" * 256, "auth_key", 1)
    async with clean_db.sessions() as session, session.begin():
        session.add(TgSession(account_id=1, dc_id=2, date=0, auth_key=foreign, user_id=EXPECTED))
        session.add(TgPeer(account_id=1, id=GAME, access_hash=42, type="bot"))
    runtime = await kurigram.start(1)
    assert runtime.facade is not None and runtime.tg is not None
    assert runtime.tg.status().state is TgState.UNAUTHORIZED
    assert set(runtime.supervisor._tasks) == KURIGRAM_TASKS
    assert await _tg_rows(clean_db) == (None, set())
    assert await _codes(clean_db) == [("error", "tg_session_unreadable")]


async def test_history_pass_runs_in_background_and_prunes_marks(
    kurigram: Engines, clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review Focus 5: сверка истории — задача аккаунта; старт движка прохода не ждёт, даже если
    # Telegram не отвечает на чтение истории.
    def hanging(transport: KurigramTransport) -> FakeClient:
        client = FakeClient(transport._storage)
        client.hang |= {"GetHistory", "Search"}
        return client

    monkeypatch.setattr(KurigramTransport, "_make_client", hanging)
    async with clean_db.sessions() as session, session.begin():
        sealed = BOX.seal(b"k" * 256, "auth_key", 1)
        session.add(TgSession(account_id=1, dc_id=2, date=0, auth_key=sealed, user_id=EXPECTED))
        # Чтение, которого больше нет в настройках, и чтение чата игры.
        session.add(TgChatMark(account_id=1, chat_id=OTHER, from_id=0, msg_id=5))
        session.add(TgChatMark(account_id=1, chat_id=GAME, from_id=0, msg_id=7))
    await kurigram.deps.accounts.bind_telegram(1, EXPECTED)
    runtime = await kurigram.start(1)
    assert runtime.tg is not None and runtime.tg.status().state is TgState.ONLINE
    assert set(runtime.supervisor._tasks) == KURIGRAM_TASKS
    client = runtime.transport._client  # type: ignore[union-attr]
    assert isinstance(client, FakeClient)
    # Проход начался сразу после выхода в онлайн и висит на чтении.
    await until(lambda: any(name in {"GetHistory", "Search"} for name, _ in client.invoked))
    assert runtime.facade is not None and runtime.facade.status().workers_ok
    async with clean_db.sessions() as session:
        marks = await session.execute(select(TgChatMark.chat_id, TgChatMark.msg_id))
        assert {(chat_id, msg_id) for chat_id, msg_id in marks} == {(GAME, 7)}


async def test_game_chat_membership_reaches_engine_status(
    kurigram: Engines, clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Пир общего чата игры неизвестен: GetChannels с access_hash=0 — CHANNEL_INVALID.
    def not_member(transport: KurigramTransport) -> FakeClient:
        client = FakeClient(transport._storage)
        client.errors["ResolvePeer"] = rpc_error("ChannelInvalid")
        client.hang |= {"GetHistory"}
        return client

    monkeypatch.setattr(KurigramTransport, "_make_client", not_member)
    async with clean_db.sessions() as session, session.begin():
        sealed = BOX.seal(b"k" * 256, "auth_key", 1)
        session.add(TgSession(account_id=1, dc_id=2, date=0, auth_key=sealed, user_id=EXPECTED))
    await kurigram.deps.accounts.bind_telegram(1, EXPECTED)
    runtime = await kurigram.start(1)
    facade = runtime.facade
    assert facade is not None
    await until(lambda: facade.status().game_chat_member is False)
    await until(
        lambda: any(
            e.type == "notification" and e.data["code"] == "game_chat_not_member"
            for e in runtime.stream.history()
        )
    )
    assert ("warn", "game_chat_not_member") in await _codes(clean_db)


async def test_self_chat_binds_and_stays_offline_until_restart(
    kurigram: Engines, clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    clients: list[FakeClient] = []

    def recording(transport: KurigramTransport) -> FakeClient:
        clients.append(FakeClient(transport._storage))
        return clients[-1]

    monkeypatch.setattr(KurigramTransport, "_make_client", recording)
    # Аккаунт не привязан, сессия Telegram есть, а чат игры в настройках — его же пользователь.
    async with clean_db.sessions() as session, session.begin():
        sealed = BOX.seal(b"k" * 256, "auth_key", 1)
        session.add(TgSession(account_id=1, dc_id=2, date=0, auth_key=sealed, user_id=EXPECTED))
    own_chat = SettingsPatch({"chats": {"game_chat_id": EXPECTED}})
    await direct_update(clean_db, 1, own_chat, changed_by="t", expected_version=0)
    runtime = await kurigram.start(1)
    assert runtime.tg is not None and runtime.facade is not None
    st = runtime.tg.status()
    assert st.state is TgState.ERROR and st.error == "chat_is_self"
    # Привязка записана в базу, в онлайн аккаунт не вышел.
    account = await kurigram.deps.accounts.get(1)
    assert account is not None and account.tg_user_id == EXPECTED
    # Клиент отключён без выхода: сессия — в базе.
    assert clients and all(not c.is_connected and not c.is_initialized for c in clients)
    assert "LogOut" not in [name for c in clients for name, _ in c.invoked]
    row, _ = await _tg_rows(clean_db)
    assert row is not None and row.user_id == EXPECTED
    assert await _codes(clean_db) == [("warn", "chat_is_self")]
    # Правка через движок сверяется с привязкой: свой чат — отказ, исправление проходит.
    with pytest.raises(ChatIsSelf):
        await runtime.facade.patch_settings(
            {"chats": {"swinfo_user_id": EXPECTED}}, version=1, by="t"
        )
    await runtime.facade.patch_settings({"chats": {"game_chat_id": GAME}}, version=1, by="t")
    # Фильтр чатов и разбор движка — с прежними чатами: до перезапуска вход — тот же отказ.
    st = await runtime.tg.start("+888", owner="s1")
    st = await runtime.tg.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.ERROR and st.error == "chat_is_self"
    assert all(not c.is_connected and not c.is_initialized for c in clients)
    await runtime.stop()
    await kurigram.leases.release(runtime.fence)
    again = await kurigram.start(1)
    assert again.tg is not None and again.tg.status().state is TgState.ONLINE


async def test_stale_unbound_snapshot_refused_by_binding_in_db(
    kurigram: Engines, clean_db: Database
) -> None:
    # Снимок аккаунта при старте — без привязки, а в базе аккаунт уже привязан к другому
    # пользователю: вход отклоняется до онлайна, сессия закрывается, привязка в базе прежняя.
    async with clean_db.sessions() as session, session.begin():
        sealed = BOX.seal(b"k" * 256, "auth_key", 1)
        session.add(TgSession(account_id=1, dc_id=2, date=0, auth_key=sealed, user_id=EXPECTED))
    fence = await kurigram.leases.acquire(1)
    assert isinstance(fence, Fence)
    account = await kurigram.deps.accounts.get(1)
    assert account is not None and account.tg_user_id is None
    await kurigram.deps.accounts.bind_telegram(1, 42)
    runtime = AccountRuntime(account, kurigram.deps, fence, on_crash_loop=kurigram._crash_loop)
    kurigram.runtimes.append(runtime)
    await runtime.start()
    assert runtime.tg is not None
    st = runtime.tg.status()
    assert st.state is TgState.ERROR and st.error == "unexpected_user"
    assert st.bound_user_id == 42 and st.user_id is None
    assert await _tg_rows(clean_db) == (None, set())
    stored = await kurigram.deps.accounts.get(1)
    assert stored is not None and stored.tg_user_id == 42


async def test_start_clamps_out_of_bounds_with_system_history(
    engines: Engines, clean_db: Database
) -> None:
    # settings.engine.min_request_interval_s = 1.0 в базе → после старта 1.6,
    # settings_history: последняя запись changed_by == "system"; движок запущен
    async with clean_db.sessions() as s, s.begin():
        data = Settings().model_dump(mode="json")
        data["engine"]["min_request_interval_s"] = 1.0
        s.add(SettingsRow(account_id=1, data=data, version=1))
    runtime = await engines.start(1)
    assert runtime.settings.current.engine.min_request_interval_s == 1.6
    assert runtime.facade is not None
    async with clean_db.sessions() as s:
        last_hist = (
            await s.scalars(
                select(SettingsHistory)
                .where(SettingsHistory.account_id == 1)
                .order_by(SettingsHistory.version.desc())
            )
        ).first()
    assert last_hist is not None
    assert last_hist.changed_by == "system"
    assert last_hist.data["engine"]["min_request_interval_s"] == 1.6
