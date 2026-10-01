import asyncio
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime
from types import SimpleNamespace as NS
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.config import AppConfig
from app.db.base import Database
from app.db.crypto import SecretBox
from app.db.models import TgPeer, TgSession
from app.engine.fence import Fence, LeaseLost
from app.engine.notify import Level
from app.engine.settings import ChatsSection
from app.engine.tg_auth import InvalidPhone, SendCodeRejected, TgAuthManager, TgState
from app.engine.transport.base import (
    FloodWait,
    GroupInfo,
    TransportAuthLost,
    TransportRejected,
)
from app.engine.transport.kurigram import ChatFilter
from app.engine.types import IncomingMessage
from tests.conftest import TEST_DB_URL
from tests.engine.helpers import GAME, until
from tests.engine.kurigram_fakes import EXPECTED, FakeClient, FakeKurigram, long_fence, rpc_error

TEAM = -1001149209877
BOX = SecretBox(secrets.token_bytes(32))


class Recorder:
    def __init__(self) -> None:
        self.items: list[tuple[Level, str]] = []

    async def notify(self, level: Level, code: str, text: str) -> None:
        self.items.append((level, code))


async def _online(t: FakeKurigram) -> list[int]:
    lost: list[int] = []

    async def on_lost() -> None:
        lost.append(1)

    t.on_auth_lost = on_lost
    assert await t.connect()
    await t.go_online()
    assert t.client.is_initialized
    return lost


def _assert_reset(t: FakeKurigram) -> None:
    old, new = t.clients
    assert old.storage.deleted and not old.is_initialized and not old.is_connected
    assert t._client is new and not new.is_connected


async def test_probe_unauthorized_resets_client_and_reports() -> None:
    t = FakeKurigram()
    lost = await _online(t)
    t.client.errors["GetState"] = rpc_error("SessionRevoked")
    await t.probe()
    assert t.clients[0].invoked[-1] == (
        "GetState",
        {"retries": 1, "sleep_threshold": 0, "retry_delay": 0},
    )
    _assert_reset(t)
    assert lost == [1]


async def test_probe_other_error_only_logged() -> None:
    t = FakeKurigram()
    lost = await _online(t)
    t.client.errors["GetState"] = OSError("network down")
    await t.probe()
    assert len(t.clients) == 1 and t.client.is_initialized and lost == []


@pytest.mark.parametrize("op", ["send", "click"])
async def test_send_click_unauthorized_resets_client(op: str) -> None:
    t = FakeKurigram()
    lost = await _online(t)
    name = "SendMessage" if op == "send" else "GetBotCallbackAnswer"
    t.client.errors[name] = rpc_error("AuthKeyUnregistered")
    with pytest.raises(TransportAuthLost):
        if op == "send":
            await t.send_text(GAME, "😎Я")
        else:
            await t.click(GAME, 1, "maze_up", 1.0)
    _assert_reset(t)
    assert lost == [1]
    assert t.clients[0].invoked[-1][1]["retry_delay"] == 0


async def test_send_and_click_use_peer_resolved_in_advance() -> None:
    # Шлюз разрешает peer заранее и проверяет команду вплотную перед RPC: между ними — ни одного
    # обращения к Telegram.
    t = FakeKurigram()
    await _online(t)
    await t.resolve(GAME)
    await t.resolve(GAME)
    assert t.client.resolved == [GAME]
    await t.send_text(GAME, "😎Я")
    await t.click(GAME, 1, "maze_up", 1.0)
    assert t.client.resolved == [GAME]
    assert [name for name, _ in t.client.invoked[-2:]] == ["SendMessage", "GetBotCallbackAnswer"]
    # Без заранее разрешённого peer — как раньше, через resolve_peer.
    await t.send_text(GAME + 1, "😎Я")
    assert t.client.resolved == [GAME, GAME + 1]


async def test_peer_cache_dropped_with_client() -> None:
    t = FakeKurigram()
    await _online(t)
    await t.resolve(GAME)
    t.client.errors["GetState"] = rpc_error("SessionRevoked")
    await t.probe()
    _assert_reset(t)
    await t.resolve(GAME)
    assert t.clients[1].resolved == [GAME]


async def test_resolve_errors() -> None:
    from pyrogram import errors

    t = FakeKurigram()
    lost = await _online(t)
    t.client.errors["ResolvePeer"] = errors.FloodWait(7)
    with pytest.raises(FloodWait):
        await t.resolve(GAME)
    t.client.errors["ResolvePeer"] = rpc_error("AuthKeyUnregistered")
    with pytest.raises(TransportAuthLost):
        await t.resolve(GAME)
    _assert_reset(t)
    assert lost == [1]


async def test_reset_survives_stop_failure() -> None:
    t = FakeKurigram()
    lost = await _online(t)
    t.client.stop_error = RuntimeError("stop failed")
    t.client.errors["GetState"] = rpc_error("SessionRevoked")
    await t.probe()
    old = t.clients[0]
    assert old.storage.deleted and t._client is t.clients[1] and lost == [1]
    # stop() бросил после частичной остановки (watchdog умер до is_initialized=False) —
    # клиент должен быть форсированно отключён и storage закрыт, иначе течёт MTProto-сессия.
    assert not old.is_initialized and not old.is_connected
    assert old.storage.closed


async def test_stop_forces_disconnect_on_failure() -> None:
    t = FakeKurigram()
    await _online(t)
    client = t.client
    client.stop_error = RuntimeError("stop failed")
    await t.stop()
    assert not client.is_initialized and not client.is_connected
    assert client.storage.closed


async def test_failed_log_out_still_resets_client() -> None:
    t = FakeKurigram()
    lost = await _online(t)
    t.client.errors["LogOut"] = ConnectionError("network down")
    with pytest.raises(ConnectionError):
        await t.log_out()
    _assert_reset(t)
    assert lost == []
    assert t.clients[0].invoked[-1] == (
        "LogOut",
        {"retries": 1, "sleep_threshold": 0, "retry_delay": 0},
    )


async def test_log_out_of_revoked_session_is_success() -> None:
    t = FakeKurigram()
    await _online(t)
    t.client.errors["LogOut"] = rpc_error("SessionRevoked")
    await t.log_out()
    _assert_reset(t)


async def test_relogin_after_loss_reaches_online() -> None:
    t = FakeKurigram()
    rec = Recorder()
    mgr = TgAuthManager(t, expected_user_id=EXPECTED, notifier=rec)
    t.on_auth_lost = mgr.mark_lost
    assert (await mgr.boot()).state is TgState.ONLINE
    t.client.errors["GetState"] = rpc_error("SessionRevoked")
    await t.probe()
    assert mgr.status().state is TgState.UNAUTHORIZED
    assert rec.items == [("error", "tg_auth_lost")]
    st = await mgr.start("+888", owner="s1")
    st = await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.ONLINE and t._client is t.clients[1]
    assert t.clients[1].is_initialized and t.clients[1].get_me_calls == 1


async def test_click_bot_response_timeout_is_no_toast() -> None:
    t = FakeKurigram()
    lost = await _online(t)
    t.client.errors["GetBotCallbackAnswer"] = rpc_error("BotResponseTimeout")
    assert await t.click(GAME, 1, "maze_up", 1.0) is None
    assert len(t.clients) == 1 and lost == []


async def test_click_other_bad_request_rejected() -> None:
    t = FakeKurigram()
    await _online(t)
    t.client.errors["GetBotCallbackAnswer"] = rpc_error("DataInvalid")
    with pytest.raises(TransportRejected, match="DATA_INVALID"):
        await t.click(GAME, 1, "maze_up", 1.0)


async def test_sdk_invoke_single_attempt_on_timeout() -> None:
    # Фиксирует поведение kurigram 2.2.x: TimeoutError — подкласс OSError, и Session.invoke
    # с retries=1 делает ровно одну попытку send, прежде чем поднять TimeoutError.
    from pyrogram import raw
    from pyrogram.session import Session

    session = Session(NS(name="t"), 2, "127.0.0.1", 443, b"\0" * 256, False)
    session.is_started.set()
    calls = 0

    async def send(data: object, wait_response: bool = True, timeout: float = 0) -> None:  # noqa: ASYNC109
        nonlocal calls
        calls += 1
        raise TimeoutError("Request timed out")

    session.send = send  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        await session.invoke(raw.functions.updates.GetState(), retries=1, retry_delay=0)
    assert calls == 1


async def test_send_code_invalid_phone_classified() -> None:
    t = FakeKurigram(authorized=False)
    await t.connect()
    t.client.errors["SendCode"] = rpc_error("PhoneNumberInvalid")
    with pytest.raises(InvalidPhone):
        await t.send_code("+1")


async def test_send_code_flood_wait_mapped() -> None:
    from pyrogram import errors

    t = FakeKurigram(authorized=False)
    await t.connect()
    t.client.errors["SendCode"] = errors.FloodWait(30)
    with pytest.raises(FloodWait) as info:
        await t.send_code("+1")
    assert info.value.seconds == 30


async def test_send_code_other_bad_request_rejected() -> None:
    t = FakeKurigram(authorized=False)
    await t.connect()
    t.client.errors["SendCode"] = rpc_error("PhoneNumberBanned")
    with pytest.raises(SendCodeRejected) as info:
        await t.send_code("+1")
    assert info.value.code == "phone_number_banned"


async def test_identify_unauthorized_resets_client_without_callback() -> None:
    t = FakeKurigram()
    lost: list[int] = []

    async def on_lost() -> None:
        lost.append(1)

    t.on_auth_lost = on_lost
    assert await t.connect()
    t.client.errors["GetMe"] = rpc_error("SessionRevoked")
    with pytest.raises(TransportAuthLost):
        await t.identify()
    _assert_reset(t)
    # identify() не должен звать on_auth_lost сам: TgAuthManager.boot() держит свой
    # lock во время identify(), а on_auth_lost обычно привязан к mark_lost(), который
    # тот же lock захватывает повторно — это дедлок (не re-entrant asyncio.Lock).
    assert lost == []


async def test_boot_revoked_session_reports_and_resets_without_deadlock() -> None:
    t = FakeKurigram()
    rec = Recorder()
    mgr = TgAuthManager(t, expected_user_id=EXPECTED, notifier=rec)
    t.on_auth_lost = mgr.mark_lost
    t.client.errors["GetMe"] = rpc_error("SessionRevoked")
    st = await mgr.boot()
    assert st.state is TgState.UNAUTHORIZED and st.error == "session_revoked"
    assert rec.items == [("error", "tg_auth_lost")]


async def test_boot_calls_get_me_once() -> None:
    t = FakeKurigram()
    mgr = TgAuthManager(t, expected_user_id=EXPECTED)
    assert (await mgr.boot()).state is TgState.ONLINE
    assert t.client.get_me_calls == 1
    assert t.client.me is not None and t.client.me.id == EXPECTED


async def test_fetch_converts_current_message() -> None:
    from datetime import datetime

    t = FakeKurigram()
    await _online(t)
    sent = datetime(2026, 9, 26, 20, 4, 52)
    edited = datetime(2026, 9, 26, 20, 5, 7)
    t.client.stored[(GAME, 7)] = NS(
        id=7,
        chat=NS(id=GAME),
        from_user=NS(id=GAME),
        outgoing=False,
        date=sent,
        edit_date=edited,
        text="🔋205%",
        caption=None,
        reply_markup=None,
        empty=False,
    )
    msg = await t.fetch(GAME, 7)
    assert msg is not None
    assert (msg.msg_id, msg.kind, msg.text) == (7, "edit", "🔋205%")
    assert msg.revision == int(msg.date.timestamp())
    assert await t.fetch(GAME, 8) is None


async def test_fetch_unauthorized_resets_client() -> None:
    t = FakeKurigram()
    lost = await _online(t)
    t.client.errors["GetMessages"] = rpc_error("AuthKeyUnregistered")
    with pytest.raises(TransportAuthLost):
        await t.fetch(GAME, 7)
    _assert_reset(t)
    assert lost == [1]


def _forwarded(new_id: int) -> object:
    from pyrogram import raw

    return raw.types.Updates(
        updates=[raw.types.UpdateMessageID(id=new_id, random_id=1)],
        users=[],
        chats=[],
        date=0,
        seq=0,
    )


async def test_forward_single_attempt_returns_destination_id() -> None:
    t = FakeKurigram()
    await _online(t)
    t.client.responses["ForwardMessages"] = _forwarded(4242)
    assert await t.forward(GAME, 77, -1001149209877) == 4242
    name, kw = t.client.invoked[-1]
    assert name == "ForwardMessages"
    assert kw == {"retries": 1, "sleep_threshold": 0, "retry_delay": 0}


async def test_forward_without_id_in_answer_is_zero() -> None:
    t = FakeKurigram()
    await _online(t)
    assert await t.forward(GAME, 77, -1001149209877) == 0


@pytest.mark.parametrize(
    ("error", "raised"),
    [
        ("ChatWriteForbidden", TransportRejected),
        ("MessageIdInvalid", TransportRejected),
        ("AuthKeyUnregistered", TransportAuthLost),
    ],
)
async def test_forward_errors_classified(error: str, raised: type[Exception]) -> None:
    t = FakeKurigram()
    await _online(t)
    t.client.errors["ForwardMessages"] = rpc_error(error)
    with pytest.raises(raised):
        await t.forward(GAME, 77, -1001149209877)


async def test_forward_flood_wait_mapped() -> None:
    from pyrogram import errors

    t = FakeKurigram()
    await _online(t)
    t.client.errors["ForwardMessages"] = errors.FloodWait(7)
    with pytest.raises(FloodWait):
        await t.forward(GAME, 77, -1001149209877)


@pytest.mark.parametrize("error", [KeyError("unknown peer"), OSError("network down")])
async def test_forward_unresolved_peer_is_refusal_not_unknown(error: Exception) -> None:
    # До ForwardMessages дело не дошло: пересылки точно нет — отказ, а не неясный исход.
    t = FakeKurigram()
    await _online(t)
    t.client.errors["ResolvePeer"] = error
    with pytest.raises(TransportRejected, match="peer"):
        await t.forward(GAME, 77, -1001149209877)
    assert all(name != "ForwardMessages" for name, _ in t.client.invoked)


def _chat(kind: str, title: str | None = "☣️ SU") -> object:
    from pyrogram import enums

    return NS(type=getattr(enums.ChatType, kind), title=title)


def _member(status: str) -> object:
    from pyrogram import enums

    return NS(status=getattr(enums.ChatMemberStatus, status))


@pytest.mark.parametrize(
    ("kind", "status", "verdict"),
    [
        ("SUPERGROUP", "MEMBER", "ok"),
        ("GROUP", "ADMINISTRATOR", "ok"),
        ("FORUM", "OWNER", "ok"),
        ("SUPERGROUP", "LEFT", "not_member"),
        ("SUPERGROUP", "BANNED", "not_member"),
        ("CHANNEL", "MEMBER", "not_group"),
        ("PRIVATE", "MEMBER", "not_group"),
    ],
)
async def test_check_group(kind: str, status: str, verdict: str) -> None:
    t = FakeKurigram()
    await _online(t)
    t.client.chat = _chat(kind)
    t.client.member = _member(status)
    # Название — чтобы опечатку в ID было видно в логе и журнале действия.
    assert await t.check_group(-1001149209877) == GroupInfo(verdict, "☣️ SU")


@pytest.mark.parametrize(
    ("where", "error", "verdict"),
    [
        ("GetChatMember", "UserNotParticipant", "not_member"),
        ("GetChat", "ChannelPrivate", "unavailable"),
        ("GetChat", "PeerIdInvalid", "unavailable"),
    ],
)
async def test_check_group_errors(where: str, error: str, verdict: str) -> None:
    t = FakeKurigram()
    await _online(t)
    t.client.chat = _chat("SUPERGROUP")
    t.client.member = _member("MEMBER")
    t.client.errors[where] = rpc_error(error)
    assert (await t.check_group(-1001149209877)).verdict == verdict


async def test_check_group_unauthorized_resets_client() -> None:
    t = FakeKurigram()
    lost = await _online(t)
    t.client.errors["GetChat"] = rpc_error("AuthKeyUnregistered")
    with pytest.raises(TransportAuthLost):
        await t.check_group(-1001149209877)
    assert lost == [1]


class FakeMonotonic:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _game_message(msg_id: int) -> object:
    return NS(
        id=msg_id,
        chat=NS(id=GAME),
        from_user=NS(id=GAME),
        outgoing=False,
        date=datetime(2026, 9, 26, 20, 4, 52),
        edit_date=None,
        text="🔋205%",
        caption=None,
        reply_markup=None,
    )


async def _drop(msg: IncomingMessage) -> None:
    return None


@pytest.mark.db
async def test_client_uses_storage_engine_and_skips_updates(db: Database) -> None:
    from app.db.tg_storage import PgSessionStorage
    from app.engine.transport.kurigram import KurigramTransport

    storage = PgSessionStorage(db, 7, BOX, set)
    t = KurigramTransport(
        api_id=1,
        api_hash="x",
        account_id=7,
        storage=storage,
        fence=long_fence(),
        chat_filter=ChatFilter.from_settings(ChatsSection()),
        sink=_drop,
    )
    client = t._client
    # Сессия — в базе, а не в файле рабочего каталога; догонки kurigram нет — пропущенное
    # возвращает сверка истории.
    assert client.name == "account-7" and client.storage is storage
    assert client.skip_updates is True and client.workers == 1
    assert not client.in_memory and client.session_string is None


def _telegram_calls(t: FakeKurigram) -> list[Callable[[], Awaitable[Any]]]:
    return [
        t.connect,
        lambda: t.send_code("+888"),
        lambda: t.sign_in("+888", "hash", "12345"),
        lambda: t.check_password("pw"),
        t.identify,
        t.go_online,
        t.log_out,
        t.probe,
        lambda: t.resolve(GAME + 1),
        lambda: t.send_text(GAME, "😎Я"),
        lambda: t.click(GAME, 1, "maze_up", 1.0),
        lambda: t.forward(GAME, 77, TEAM),
        lambda: t.check_group(TEAM),
        lambda: t.fetch(GAME, 7),
    ]


async def test_calls_refused_after_fence_deadline() -> None:
    clock = FakeMonotonic()
    lost: list[int] = []
    fence = Fence(1, 1, clock.now + 10.0, monotonic=clock)
    fence.on_lost = lambda: lost.append(1)
    t = FakeKurigram(fence=fence)
    await _online(t)
    client = t.client
    invoked, resolved, me = list(client.invoked), list(client.resolved), client.get_me_calls
    clock.now += 10.0
    for call in _telegram_calls(t):
        with pytest.raises(LeaseLost):
            await call()
    # Ни один вызов не дошёл до клиента, клиент не сброшен.
    assert client.invoked == invoked and client.resolved == resolved
    assert client.get_me_calls == me and t.clients == [client] and client.is_initialized
    assert lost == [1]


async def test_call_cut_when_invoke_hangs_past_deadline() -> None:
    clock = FakeMonotonic()
    fence = Fence(1, 1, clock.now + 3600.0, monotonic=clock)
    t = FakeKurigram(fence=fence)
    await _online(t)
    t.client.hang.add("SendMessage")
    # До срока — 50 мс: invoke kurigram сам ждал бы запуска сессии до 15 с.
    clock.now = fence.deadline - 0.05
    with pytest.raises(LeaseLost):
        await asyncio.wait_for(t.send_text(GAME, "😎Я"), 5.0)
    assert t.client.cancelled == ["SendMessage"] and not fence.alive


async def test_abort_stops_session_cancels_handlers_without_terminate() -> None:
    received: list[int] = []

    async def sink(msg: IncomingMessage) -> None:
        received.append(msg.msg_id)

    t = FakeKurigram(sink=sink)
    client = t.client
    client.dispatcher.handler = lambda update: t._on_new(client, update)
    await _online(t)
    client.dispatcher.updates_queue.put_nowait(_game_message(1))
    await until(lambda: received == [1])
    # Принятое до остановки: обработчик диспетчера разобрал бы его, пока останавливается сессия.
    for msg_id in (2, 3):
        client.dispatcher.updates_queue.put_nowait(_game_message(msg_id))
    t.events.clear()
    await t.abort()
    # Без terminate(): он сохранил бы хранилище и доработал очередь диспетчера в конвейер.
    assert t.events == ["session.stop", "handler.cancelled", "storage.close"]
    assert received == [1]
    assert all(task.done() for task in client.dispatcher.handler_worker_tasks)

    # Клиент не подключался: сессии нет — закрывается только хранилище.
    idle = FakeKurigram()
    await idle.abort()
    assert idle.events == ["storage.close"]


def _kurigram_config() -> AppConfig:
    return AppConfig(
        _env_file=None,
        database_url=TEST_DB_URL,
        transport="kurigram",
        tg_api_id=1,
        tg_api_hash=SecretStr("x"),
    )


async def _session_rows(db: Database) -> tuple[list[TgSession], list[TgPeer]]:
    async with db.sessions() as session:
        sessions = (await session.scalars(select(TgSession))).all()
        peers = (await session.scalars(select(TgPeer))).all()
    return list(sessions), list(peers)


@pytest.mark.db
async def test_logout_offline_logs_out_and_deletes_storage(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pyrogram

    from app.db.tg_storage import PgSessionStorage
    from app.engine.transport.kurigram import logout_offline

    stored = PgSessionStorage(clean_db, 1, BOX, lambda: {GAME})
    await stored.open()
    await stored.auth_key(b"k" * 256)
    await stored.user_id(EXPECTED)
    await stored.update_peers([(GAME, 42, "user", None)])
    made: list[tuple[str, dict[str, Any], FakeClient]] = []

    def client(name: str, **kwargs: Any) -> FakeClient:
        fake = FakeClient(kwargs["storage_engine"])
        made.append((name, kwargs, fake))
        return fake

    monkeypatch.setattr(pyrogram, "Client", client)
    await logout_offline(clean_db, BOX, _kurigram_config(), 1)
    [(name, kwargs, fake)] = made
    assert name == "account-1" and isinstance(kwargs["storage_engine"], PgSessionStorage)
    assert fake.invoked == [("LogOut", {"retries": 1, "sleep_threshold": 0, "retry_delay": 0})]
    assert not fake.is_connected
    assert await _session_rows(clean_db) == ([], [])
    # Сессии в базе нет — временный клиент не поднимается.
    await logout_offline(clean_db, BOX, _kurigram_config(), 1)
    assert len(made) == 1
