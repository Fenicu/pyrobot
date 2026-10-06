import asyncio
import secrets
from collections.abc import Awaitable, Callable, Sequence
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
from app.engine.bus import Bus
from app.engine.fence import Fence, LeaseLost
from app.engine.host.account import pipeline_deliver
from app.engine.memory import MemoryJournal
from app.engine.notify import Level
from app.engine.parsing import default_parser
from app.engine.pipeline import NullReducer, Pipeline
from app.engine.settings import ChatsSection
from app.engine.tg_auth import (
    AttemptMismatch,
    InvalidPhone,
    SendCodeRejected,
    SentCodeInfo,
    TgAuthManager,
    TgState,
)
from app.engine.transport.base import (
    ChatUnavailable,
    FloodWait,
    GroupInfo,
    TransportAuthLost,
    TransportRejected,
)
from app.engine.transport.history import HistorySync
from app.engine.transport.kurigram import OVERLOAD_HIGH, OVERLOAD_LOW, ChatFilter
from app.engine.types import IncomingMessage
from tests.conftest import TEST_DB_URL
from tests.engine.helpers import GAME, tg_auth, until
from tests.engine.kurigram_fakes import EXPECTED, FakeClient, FakeKurigram, long_fence, rpc_error
from tests.engine.test_history_sync import FakeSource, MemoryMarks

TEAM = -1001149209877
SWINFO = -1001109615116
SW_USER = 376592453
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


async def test_log_out_after_disconnect_reaches_telegram() -> None:
    # Отключение без выхода (свой чат в настройках): клиент новый, не подключён, а сессия — в
    # хранилище; выход подключает его только для `auth.LogOut`.
    t = FakeKurigram()
    assert await t.connect()
    await t.disconnect()
    assert not t.client.is_connected and not t.storage.deleted
    await t.log_out()
    assert [name for name, _ in t.clients[1].invoked] == ["LogOut"]
    assert t.storage.deleted and len(t.clients) == 3
    assert all(not c.is_connected for c in t.clients)


async def test_set_app_recreates_client_with_new_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[int, str]] = []
    make_client = FakeKurigram._make_client

    def recorded(t: FakeKurigram) -> FakeClient:
        seen.append((t._api_id, t._api_hash))
        return make_client(t)

    monkeypatch.setattr(FakeKurigram, "_make_client", recorded)
    t = FakeKurigram(authorized=False)
    old = t.client
    assert not await t.connect()
    await t.set_app(12345, "a" * 32)
    assert t.client is not old and not old.is_connected
    assert (t._api_id, t._api_hash) == (12345, "a" * 32)
    assert seen == [(1, "x"), (12345, "a" * 32)]
    assert not t.storage.deleted


async def test_set_app_swaps_client_before_closing_old(monkeypatch: pytest.MonkeyPatch) -> None:
    # Параллельный connect() не должен подхватить закрывающийся клиент: подмена раньше закрытия.
    t = FakeKurigram(authorized=False)
    old = t.client
    assert not await t.connect()
    seen: list[bool] = []
    disconnect = old.disconnect

    async def watched() -> None:
        seen.append(t._client is not old)
        await disconnect()

    monkeypatch.setattr(old, "disconnect", watched)
    await t.set_app(12345, "a" * 32)
    assert seen == [True] and not old.is_connected


async def test_set_app_refused_when_logged_in() -> None:
    from app.engine.transport.kurigram import TgLoggedIn

    t = FakeKurigram()
    old = t.client
    with pytest.raises(TgLoggedIn):
        await t.set_app(12345, "a" * 32)
    assert t.client is old and t._api_id == 1


async def test_log_out_without_session_does_not_connect() -> None:
    # Сессии в хранилище нет (уже вышли): выходить у Telegram нечем — клиент не подключается.
    t = FakeKurigram()
    await _online(t)
    await t.log_out()
    await t.log_out()
    assert len(t.clients) == 3
    assert not t.clients[1].is_connected and t.clients[1].invoked == []


async def test_log_out_of_revoked_session_is_success() -> None:
    t = FakeKurigram()
    await _online(t)
    t.client.errors["LogOut"] = rpc_error("SessionRevoked")
    await t.log_out()
    _assert_reset(t)


async def test_relogin_after_loss_reaches_online() -> None:
    t = FakeKurigram()
    rec = Recorder()
    mgr = tg_auth(t, expected_user_id=EXPECTED, notifier=rec)
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


async def test_send_code_returns_sent_code_info() -> None:
    from pyrogram import raw

    t = FakeKurigram(authorized=False)
    await t.connect()
    info = await t.send_code("+1234567890")
    assert info == SentCodeInfo(phone_code_hash="hash", type="app")

    t.client.responses["SendCode"] = raw.types.auth.SentCode(
        type=raw.types.auth.SentCodeTypeEmailCode(email_pattern="f***n@g***.com", length=6),
        phone_code_hash="h_email",
        next_type=raw.types.auth.CodeTypeSms(),
        timeout=90,
    )
    assert await t.send_code("+1234567890") == SentCodeInfo(
        phone_code_hash="h_email",
        type="email",
        email_pattern="f***n@g***.com",
        next_type="sms",
        timeout=90,
    )

    t.client.responses["SendCode"] = raw.types.auth.SentCode(
        type=raw.types.auth.SentCodeTypeSetUpEmailRequired(), phone_code_hash="h_setup"
    )
    assert await t.send_code("+1234567890") == SentCodeInfo(
        phone_code_hash="h_setup", type="setup_email"
    )


async def test_send_code_unavailable_is_rejected_not_failed() -> None:
    t = FakeKurigram(authorized=False)
    await t.connect()
    t.client.errors["SendCode"] = rpc_error("SendCodeUnavailable")
    with pytest.raises(SendCodeRejected) as info:
        await t.send_code("+1")
    assert info.value.code == "send_code_unavailable"


@pytest.mark.parametrize("kind", ["success", "payment_required"])
async def test_send_code_unsupported_answer(kind: str, caplog: pytest.LogCaptureFixture) -> None:
    from pyrogram import raw

    answer: Any = (
        raw.types.auth.SentCodeSuccess(
            authorization=raw.types.auth.Authorization(user=_user_raw())
        )
        if kind == "success"
        else raw.types.auth.SentCodePaymentRequired(
            store_product="p",
            phone_code_hash="h",
            support_email_address="s@example.com",
            support_email_subject="s",
            premium_days=7,
            currency="USD",
            amount=100,
        )
    )
    t = FakeKurigram(authorized=False)
    await t.connect()
    t.client.responses["SendCode"] = answer
    with pytest.raises(SendCodeRejected) as info:
        await t.send_code("+1")
    assert info.value.code == f"send_code_unsupported:sent_code_{kind}"
    assert f"sent_code_{kind}" in caplog.text
    t.client.responses["ResendCode"] = answer
    with pytest.raises(SendCodeRejected) as info:
        await t.resend_code("+1", "h")
    assert info.value.code == f"send_code_unsupported:sent_code_{kind}"


async def test_resend_code_invokes_rpc() -> None:
    from pyrogram import raw

    t = FakeKurigram(authorized=False)
    await t.connect()
    t.client.responses["ResendCode"] = raw.types.auth.SentCode(
        type=raw.types.auth.SentCodeTypeSms(length=5),
        phone_code_hash="new_hash",
        next_type=raw.types.auth.CodeTypeCall(),
        timeout=120,
    )
    info = await t.resend_code("+1 (234) 567-890", "old_hash")
    assert info == SentCodeInfo(
        phone_code_hash="new_hash", type="sms", next_type="call", timeout=120
    )
    query = t.client.queries[-1]
    assert isinstance(query, raw.functions.auth.ResendCode)
    assert (query.phone_number, query.phone_code_hash) == ("1234567890", "old_hash")


async def test_resend_code_unavailable_is_rejected_not_failed() -> None:
    t = FakeKurigram(authorized=False)
    await t.connect()
    t.client.errors["ResendCode"] = rpc_error("SendCodeUnavailable")
    with pytest.raises(SendCodeRejected) as info:
        await t.resend_code("+1", "h")
    assert info.value.code == "send_code_unavailable"


async def test_send_verify_email_code_invokes_rpc() -> None:
    from pyrogram import raw

    t = FakeKurigram(authorized=False)
    await t.connect()
    t.client.responses["SendVerifyEmailCode"] = raw.types.account.SentEmailCode(
        email_pattern="f***n@g***.com",
        length=6,
    )
    pattern = await t.send_verify_email_code("+1234567890", "h", " test@example.com ")
    assert pattern == "f***n@g***.com"
    query = t.client.queries[-1]
    assert isinstance(query, raw.functions.account.SendVerifyEmailCode)
    assert isinstance(query.purpose, raw.types.EmailVerifyPurposeLoginSetup)
    assert (query.purpose.phone_number, query.purpose.phone_code_hash, query.email) == (
        "1234567890",
        "h",
        "test@example.com",
    )


async def test_verify_email_returns_user_or_sent_code() -> None:
    from pyrogram import raw

    t = FakeKurigram(authorized=False)
    await t.connect()
    # 1. returns SentCode
    t.client.responses["VerifyEmail"] = raw.types.account.EmailVerifiedLogin(
        email="test@example.com",
        sent_code=raw.types.auth.SentCode(
            type=raw.types.auth.SentCodeTypeApp(length=5),
            phone_code_hash="after_email",
            timeout=60,
        ),
    )
    res = await t.verify_email("+1234567890", "h", " 12345 ")
    assert res == SentCodeInfo(phone_code_hash="after_email", type="app", timeout=60)
    query = t.client.queries[-1]
    assert isinstance(query, raw.functions.account.VerifyEmail)
    assert isinstance(query.purpose, raw.types.EmailVerifyPurposeLoginSetup)
    assert (query.purpose.phone_number, query.purpose.phone_code_hash) == ("1234567890", "h")
    assert query.verification == raw.types.EmailVerificationCode(code="12345")

    # 2. returns SentCodeSuccess
    t.client.responses["VerifyEmail"] = raw.types.account.EmailVerifiedLogin(
        email="test@example.com",
        sent_code=raw.types.auth.SentCodeSuccess(
            authorization=raw.types.auth.Authorization(user=_user_raw())
        ),
    )
    user_id = await t.verify_email("+1234567890", "h", "12345")
    assert user_id == EXPECTED
    assert await t.storage.user_id() == EXPECTED


async def test_sign_in_email_invokes_rpc() -> None:
    from pyrogram import raw

    t = FakeKurigram(authorized=False)
    await t.connect()
    t.client.responses["SignIn"] = raw.types.auth.Authorization(user=_user_raw())
    user_id = await t.sign_in("+1234567890", "h", " 12345 ", is_email=True)
    assert user_id == EXPECTED
    assert await t.storage.user_id() == EXPECTED
    query = t.client.queries[-1]
    assert isinstance(query, raw.functions.auth.SignIn)
    assert (query.phone_number, query.phone_code_hash, query.phone_code) == (
        "1234567890",
        "h",
        None,
    )
    assert query.email_verification == raw.types.EmailVerificationCode(code="12345")


def _user_raw() -> Any:
    from pyrogram import raw

    return raw.types.User(id=EXPECTED)


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
    mgr = tg_auth(t, expected_user_id=EXPECTED, notifier=rec)
    t.on_auth_lost = mgr.mark_lost
    t.client.errors["GetMe"] = rpc_error("SessionRevoked")
    st = await mgr.boot()
    assert st.state is TgState.UNAUTHORIZED and st.error == "session_revoked"
    assert rec.items == [("error", "tg_auth_lost")]


async def test_boot_calls_get_me_once() -> None:
    t = FakeKurigram()
    mgr = tg_auth(t, expected_user_id=EXPECTED)
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


@pytest.mark.parametrize("where", ["ResolvePeer", "Search"])
@pytest.mark.parametrize(
    "error", ["ChannelInvalid", "ChannelPrivate", "PeerIdInvalid", "UserNotParticipant"]
)
async def test_history_of_unavailable_chat(where: str, error: str) -> None:
    # Пир чата неизвестен (GetChannels с access_hash=0) или чат закрыт для аккаунта: сверка
    # пишет одну строку и не уходит в backoff — ей нужно отличать это от прочих сбоев.
    t = FakeKurigram()
    await _online(t)
    t.client.errors[where] = rpc_error(error)
    with pytest.raises(ChatUnavailable) as caught:
        await t.latest((SWINFO, SW_USER))
    assert (caught.value.chat_id, caught.value.reason) == (SWINFO, error)


async def test_history_other_errors_are_not_chat_unavailable() -> None:
    from pyrogram import errors

    t = FakeKurigram()
    await _online(t)
    t.client.errors["GetHistory"] = rpc_error("MsgIdInvalid")
    with pytest.raises(errors.MsgIdInvalid):
        await t.latest((GAME, 0))
    t.client.errors["GetHistory"] = errors.FloodWait(7)
    with pytest.raises(FloodWait):
        await t.latest((GAME, 0))


async def test_join_chat_with_other_id_does_not_join() -> None:
    # Username мог смениться владельцем: вступать в чужой чат нельзя.
    t = FakeKurigram()
    await _online(t)
    t.client.chat = NS(id=SWINFO - 1)
    with pytest.raises(TransportRejected, match="chat_mismatch"):
        await t.join_chat("startupwarschat", SWINFO)
    assert t.client.joined == []


async def test_join_chat_joined() -> None:
    t = FakeKurigram()
    await _online(t)
    t.client.chat = NS(id=SWINFO)
    assert await t.join_chat("startupwarschat", SWINFO) == "joined"
    assert t.client.joined == ["startupwarschat"]


@pytest.mark.parametrize(
    ("error", "status"),
    [("UserAlreadyParticipant", "already_member"), ("InviteRequestSent", "request_sent")],
)
async def test_join_chat_status_from_error(error: str, status: str) -> None:
    t = FakeKurigram()
    await _online(t)
    t.client.chat = NS(id=SWINFO)
    t.client.errors["JoinChat"] = rpc_error(error)
    assert await t.join_chat("startupwarschat", SWINFO) == status


async def test_join_chat_request_sent_result() -> None:
    from pyrogram import types

    t = FakeKurigram()
    await _online(t)
    t.client.chat = NS(id=SWINFO)
    t.client.join_result = types.ChatJoinResultRequestSent()
    assert await t.join_chat("startupwarschat", SWINFO) == "request_sent"


async def test_join_chat_guard_bot_approval_is_request_sent() -> None:
    from pyrogram import types

    t = FakeKurigram()
    await _online(t)
    t.client.chat = NS(id=SWINFO)
    t.client.join_result = types.ChatJoinResultGuardBotApprovalRequired(
        bot=NS(id=1), url="https://t.me/guard", query_id="q"
    )
    assert await t.join_chat("startupwarschat", SWINFO) == "request_sent"


async def test_join_chat_declined_is_rejected() -> None:
    from pyrogram import types

    t = FakeKurigram()
    await _online(t)
    t.client.chat = NS(id=SWINFO)
    t.client.join_result = types.ChatJoinResultDeclined()
    with pytest.raises(TransportRejected) as caught:
        await t.join_chat("startupwarschat", SWINFO)
    assert str(caught.value) == "join_declined"


@pytest.mark.parametrize("where", ["GetChat", "JoinChat"])
async def test_join_chat_flood_wait(where: str) -> None:
    from pyrogram import errors

    t = FakeKurigram()
    await _online(t)
    t.client.chat = NS(id=SWINFO)
    t.client.errors[where] = errors.FloodWait(7)
    with pytest.raises(FloodWait) as caught:
        await t.join_chat("startupwarschat", SWINFO)
    assert caught.value.seconds == 7.0


@pytest.mark.parametrize(
    ("where", "error", "code"),
    [
        ("GetChat", "UsernameNotOccupied", "USERNAME_NOT_OCCUPIED"),
        ("JoinChat", "ChannelsTooMuch", "CHANNELS_TOO_MUCH"),
        ("JoinChat", "UserBannedInChannel", "USER_BANNED_IN_CHANNEL"),
    ],
)
async def test_join_chat_refusals(where: str, error: str, code: str) -> None:
    t = FakeKurigram()
    await _online(t)
    t.client.chat = NS(id=SWINFO)
    t.client.errors[where] = rpc_error(error)
    with pytest.raises(TransportRejected, match=code):
        await t.join_chat("startupwarschat", SWINFO)


async def test_join_chat_unauthorized_resets_client() -> None:
    t = FakeKurigram()
    lost = await _online(t)
    t.client.chat = NS(id=SWINFO)
    t.client.errors["JoinChat"] = rpc_error("AuthKeyUnregistered")
    with pytest.raises(TransportAuthLost):
        await t.join_chat("startupwarschat", SWINFO)
    _assert_reset(t)
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
    from app.engine.transport.kurigram import CountingQueue, KurigramTransport

    storage = PgSessionStorage(db, 7, BOX, set)
    t = KurigramTransport(
        api_id=1,
        api_hash="x",
        account_id=7,
        storage=storage,
        fence=long_fence(),
        chat_filter=ChatFilter.from_settings(ChatsSection()),
        sink=_drop,
        backlog=lambda: 0,
    )
    client = t._client
    # Сессия — в базе, а не в файле рабочего каталога; догонки kurigram нет — пропущенное
    # возвращает сверка истории.
    assert client.name == "account-7" and client.storage is storage
    assert client.skip_updates is True and client.workers == 1
    assert not client.in_memory and client.session_string is None
    # Очередь обновлений диспетчера kurigram — с подсчётом перегрузки.
    assert isinstance(client.dispatcher.updates_queue, CountingQueue)


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


class OverloadRig:
    """Транспорт, у клиентов которого обработчик диспетчера передаёт обновления в конвейер, —
    список, который тест разбирает сам (`backlog` — его длина); `gate` держит передачу; вход —
    `TgAuthManager`."""

    def __init__(self, fence: Fence | None = None) -> None:
        self.pipeline: list[int] = []
        self.history: list[str] = []
        self.gate = asyncio.Event()
        self.gate.set()
        self.t = FakeKurigram(
            fence=fence, sink=self._submit, backlog=lambda: len(self.pipeline), dispatch=True
        )
        self.t.overload_check_s = 0.005
        self.t.on_history_needed = self.history.append
        self.notes = Recorder()
        self.tg = tg_auth(self.t, expected_user_id=EXPECTED, notifier=self.notes)
        _wire(self.t, self.tg)

    async def _submit(self, msg: IncomingMessage) -> None:
        await self.gate.wait()
        self.pipeline.append(msg.msg_id)

    async def overload(self, *, until_event: str | None = "storage.close") -> FakeClient:
        """Онлайн, затем поток обновлений выше порога; ждёт события остановки приёма."""
        assert (await self.tg.boot()).state is TgState.ONLINE
        client = self.t.client
        self.t.events.clear()
        _burst(self.t, 1, OVERLOAD_HIGH)
        assert self.t.online
        _burst(self.t, OVERLOAD_HIGH + 1, 1)
        assert not self.t.online
        if until_event is not None:
            await until(lambda: until_event in self.t.events, timeout=5.0)
        return client


def _wire(t: FakeKurigram, tg: TgAuthManager) -> None:
    t.on_auth_lost = tg.mark_lost
    t.on_overload = tg.mark_overload
    t.on_resumed = tg.mark_resumed


def _burst(t: FakeKurigram, first: int, count: int) -> None:
    """Обновления от сетевого слоя kurigram подряд, без передачи управления."""
    queue = t.client.dispatcher.updates_queue
    for msg_id in range(first, first + count):
        queue.put_nowait(_game_message(msg_id))


async def test_overload_stop_order_and_drain() -> None:
    rig = OverloadRig()
    client = await rig.overload()
    # Сессия перестаёт принимать, диспетчер разбирает принятое, клиент отключается; хранилище
    # закрыто, но не удалено — выхода из Telegram нет.
    assert rig.t.events == [
        "session.stop",
        "terminate",
        "storage.save",
        "disconnect",
        "storage.close",
    ]
    assert not client.is_connected and not client.is_initialized and not rig.t.storage.deleted
    assert rig.pipeline == list(range(1, OVERLOAD_HIGH + 2))
    st = rig.tg.status()
    assert st.state is TgState.OVERLOAD and st.user_id == EXPECTED
    assert rig.notes.items == [("warn", "account_overload")]
    # Пока накопленное не разобрано, клиент не возвращается, а входа заново нет.
    with pytest.raises(AttemptMismatch):
        await rig.tg.start("+888", owner="s1")
    await asyncio.sleep(0.05)
    assert rig.t.clients == [client] and rig.tg.status().state is TgState.OVERLOAD
    await rig.t.stop()


async def test_resume_below_low_with_new_client_same_storage() -> None:
    rig = OverloadRig()
    old = await rig.overload()
    # Ровно OVERLOAD_LOW — ещё не «ниже».
    del rig.pipeline[:-OVERLOAD_LOW]
    await asyncio.sleep(0.05)
    assert rig.t.clients == [old] and rig.tg.status().state is TgState.OVERLOAD
    rig.pipeline.pop()
    await until(lambda: rig.tg.status().state is TgState.ONLINE)
    new = rig.t.client
    # Новый объект клиента на том же хранилище: сессия та же, кода не просили.
    assert rig.t.clients == [old, new] and new.storage is old.storage is rig.t.storage
    assert not rig.t.storage.deleted and new.is_connected and new.is_initialized and rig.t.online
    assert [name for c in rig.t.clients for name, _ in c.invoked if name == "SendCode"] == []
    assert rig.tg.status().user_id == EXPECTED
    assert rig.notes.items == [("warn", "account_overload")]
    # Пропущенное за перегрузку вернёт проход сверки истории.
    assert rig.history == ["online", "online"]
    _burst(rig.t, OVERLOAD_HIGH + 2, 1)
    await until(lambda: rig.pipeline[-1] == OVERLOAD_HIGH + 2)
    await rig.t.stop()


async def test_resume_refused_after_fence_deadline() -> None:
    # Подключение нового клиента — вызовы Telegram: после срока аренды они не начинаются.
    clock = FakeMonotonic()
    fence = Fence(1, 1, clock.now + 3600.0, monotonic=clock)
    rig = OverloadRig(fence=fence)
    await rig.overload()
    clock.now = fence.deadline
    rig.pipeline.clear()
    await until(lambda: len(rig.t.clients) == 2)
    await asyncio.sleep(0.01)
    assert not rig.t.client.is_connected and rig.t.client.invoked == []
    assert rig.tg.status().state is TgState.OVERLOAD
    await rig.t.abort()


async def test_resume_retries_after_transient_failure() -> None:
    rig = OverloadRig()
    old = await rig.overload()
    rig.t.overload_retry_s = (0.2,)
    # Первый новый клиент не выходит в онлайн из-за сбоя связи: статус остаётся перегрузкой,
    # повтор — по паузе, со следующим новым клиентом на том же хранилище.
    rig.t.client_errors["GetState"] = OSError("network down")
    rig.pipeline.clear()
    await until(lambda: len(rig.t.clients) == 2 and not rig.t.clients[1].is_connected)
    assert rig.tg.status().state is TgState.OVERLOAD and rig.history == ["online"]
    await until(lambda: rig.tg.status().state is TgState.ONLINE)
    failed, new = rig.t.clients[1:]
    assert rig.t.clients == [old, failed, new] and new.is_initialized and rig.t.online
    assert not rig.t.storage.deleted and new.storage is rig.t.storage
    assert [name for c in rig.t.clients for name, _ in c.invoked if name == "SendCode"] == []
    assert rig.history == ["online", "online"]
    assert rig.notes.items == [("warn", "account_overload")]
    await rig.t.stop()


async def test_resume_with_revoked_session_loses_auth() -> None:
    rig = OverloadRig()
    await rig.overload()
    rig.t.client_errors["GetState"] = rpc_error("SessionRevoked")
    rig.pipeline.clear()
    await until(lambda: rig.tg.status().state is TgState.UNAUTHORIZED)
    assert rig.tg.status().error == "session_revoked" and rig.t.storage.deleted
    assert rig.notes.items == [("warn", "account_overload"), ("error", "tg_auth_lost")]
    # Повтора нет: вход нужен заново.
    await asyncio.sleep(0.05)
    assert len(rig.t.clients) == 3 and not rig.t.client.is_connected
    await rig.t.stop()


async def test_log_out_during_overload_closes_telegram_session() -> None:
    rig = OverloadRig()
    old = await rig.overload()
    st = await rig.tg.logout()
    assert st.state is TgState.UNAUTHORIZED and rig.t.storage.deleted
    # Отключённый клиент перегрузки выходить не может: выход — через новый подключённый клиент.
    logout = rig.t.clients[1]
    assert old.invoked[-1][0] != "LogOut"
    assert logout.invoked == [("LogOut", {"retries": 1, "sleep_threshold": 0, "retry_delay": 0})]
    assert not logout.is_connected
    assert [name for c in rig.t.clients for name, _ in c.invoked if name == "SendCode"] == []
    # Возврат клиента снят: разобранная очередь его не поднимает.
    rig.pipeline.clear()
    await asyncio.sleep(0.05)
    assert len(rig.t.clients) == 3 and not rig.t.client.is_connected
    assert rig.notes.items == [("warn", "account_overload")]
    await rig.t.stop()


LOGOUT = ("LogOut", {"retries": 1, "sleep_threshold": 0, "retry_delay": 0})
SHED = ["session.stop", "terminate", "storage.save", "disconnect", "storage.close"]


async def test_stop_during_drain_lets_terminate_finish() -> None:
    # Штатная остановка посреди terminate(): принятое разбирается до конца, а не обрывается.
    rig = OverloadRig()
    rig.gate.clear()
    old = await rig.overload(until_event="terminate")
    stopping = asyncio.create_task(rig.t.stop())
    await asyncio.sleep(0.01)
    assert not stopping.done()
    rig.gate.set()
    await asyncio.wait_for(stopping, 5.0)
    assert rig.pipeline == list(range(1, OVERLOAD_HIGH + 2)) and rig.t.events == SHED
    assert rig.t.clients == [old] and not old.is_connected and not old.is_initialized
    assert all(task.done() for task in old.dispatcher.handler_worker_tasks)


async def test_log_out_during_drain_lets_terminate_finish() -> None:
    rig = OverloadRig()
    rig.gate.clear()
    old = await rig.overload(until_event="terminate")
    logging_out = asyncio.create_task(rig.tg.logout())
    await asyncio.sleep(0.01)
    assert not logging_out.done()
    rig.gate.set()
    st = await asyncio.wait_for(logging_out, 5.0)
    assert st.state is TgState.UNAUTHORIZED and st.error is None and rig.t.storage.deleted
    assert rig.pipeline == list(range(1, OVERLOAD_HIGH + 2))
    assert not old.is_connected and rig.t.clients[1].invoked == [LOGOUT]
    assert [name for c in rig.t.clients for name, _ in c.invoked if name == "SendCode"] == []
    await rig.t.stop()


async def test_overload_cleared_when_cancelled_before_it_starts() -> None:
    # Выход сразу за перегрузкой — её задача снята, ни разу не начавшись; после нового входа
    # перегрузка снова считается.
    rig = OverloadRig()
    await rig.overload(until_event=None)
    assert (await rig.tg.logout()).state is TgState.UNAUTHORIZED
    assert rig.pipeline == list(range(1, OVERLOAD_HIGH + 2))
    assert [c for c in rig.t.clients if LOGOUT in c.invoked] == [rig.t.clients[1]]
    st = await rig.tg.start("+888", owner="s1")
    st = await rig.tg.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.ONLINE and rig.t.online
    rig.pipeline.clear()
    _burst(rig.t, 1, OVERLOAD_HIGH + 1)
    assert not rig.t.online
    await until(lambda: rig.tg.status().state is TgState.OVERLOAD)
    await rig.t.stop()


async def test_abort_during_overload_waits_for_session_stop() -> None:
    # Аварийная остановка посреди остановки сессии: повторный stop() останавливающейся сессии
    # ничего не делает — хранилище закрывается только после того, как сессия остановилась.
    rig = OverloadRig()
    assert (await rig.tg.boot()).state is TgState.ONLINE
    session = rig.t.client.session
    assert session is not None
    hold = session.hold = asyncio.Event()
    rig.t.events.clear()
    _burst(rig.t, 1, OVERLOAD_HIGH + 1)
    await until(lambda: session.stopped)
    aborting = asyncio.create_task(rig.t.abort())
    await asyncio.sleep(0.01)
    assert not aborting.done() and rig.t.events == []
    hold.set()
    await asyncio.wait_for(aborting, 5.0)
    # Без terminate(): принятое в конвейер больше не передаётся.
    assert rig.t.events == ["session.stop", "handler.cancelled", "storage.close"]


async def test_history_pass_does_not_reenter_overload() -> None:
    # Проход после перегрузки — до 1000 сообщений новее отметки и 50 правок перед ней — идёт
    # через ограниченную очередь конвейера вместе с живыми обновлениями и ниже OVERLOAD_HIGH.
    gate = asyncio.Event()
    gate.set()

    class GatedJournal(MemoryJournal):
        async def append(self, *args: Any, **kwargs: Any) -> int | None:
            await gate.wait()
            return await super().append(*args, **kwargs)

    journal = GatedJournal()

    async def known(chat_id: int, keys: Sequence[tuple[int, int, str]]) -> set[Any]:
        stored = {
            (m.msg_id, m.revision, m.content_hash())
            for m, _ in journal.rows
            if m.chat_id == chat_id
        }
        return {key for key in keys if key in stored}

    pipeline = Pipeline(journal=journal, parser=default_parser(), reducer=NullReducer(), bus=Bus())
    t = FakeKurigram(sink=pipeline.submit, backlog=pipeline.backlog, dispatch=True)
    t.overload_check_s = 0.005
    notes = Recorder()
    tg = tg_auth(t, expected_user_id=EXPECTED, notifier=notes)
    _wire(t, tg)
    source = FakeSource()
    for msg_id in range(1, 1001):
        source.post(GAME, msg_id)
    marks = MemoryMarks({(GAME, 0): 1000})
    sync = HistorySync(
        source,
        marks,  # type: ignore[arg-type]
        known,
        pipeline_deliver(pipeline),
        ChatFilter.from_settings(ChatsSection()).accepts,
        notes,
        {(GAME, 0)},
        lambda: t.online,
    )
    t.on_history_needed = sync.request
    tasks = [asyncio.create_task(pipeline.run()), asyncio.create_task(sync.run())]
    try:
        assert (await tg.boot()).state is TgState.ONLINE
        await until(lambda: source.reads == 1)
        gate.clear()
        _burst(t, 10_001, OVERLOAD_HIGH + 1)
        await until(lambda: "storage.close" in t.events, timeout=5.0)
        assert tg.status().state is TgState.OVERLOAD
        # Пропущенное за перегрузку: новые сообщения и правки перед отметкой.
        for msg_id in range(1001, 2001):
            source.post(GAME, msg_id)
        for msg_id in range(951, 1001):
            source.edit(GAME, msg_id, f"m{msg_id} правка")
        source.gate = asyncio.Event()
        gate.set()
        await until(lambda: tg.status().state is TgState.ONLINE and source.reads == 2, 5.0)
        await until(lambda: pipeline.unfinished == 0, 5.0)
        gate.clear()
        source.gate.set()
        await until(lambda: pipeline.unfinished == 1050)
        _burst(t, 20_001, 100)
        await until(lambda: pipeline.unfinished == 1150)
        assert t.online and len(t.clients) == 2 and t.events.count("session.stop") == 1
        assert tg.status().state is TgState.ONLINE
        assert notes.items == [("warn", "account_overload")]
        gate.set()
        await until(lambda: marks.marks[(GAME, 0)] == 2000, 5.0)
        assert {m.msg_id for m, _ in journal.rows} >= set(range(951, 2001))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await t.stop()


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


@pytest.mark.db
async def test_logout_offline_uses_account_app(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pyrogram

    from app.db.accounts import AccountRepo
    from app.db.tg_storage import PgSessionStorage
    from app.engine.transport.kurigram import logout_offline

    await AccountRepo(clean_db).set_tg_app(1, 12345, BOX.seal(b"a" * 32, "tg_api_hash", 1))
    stored = PgSessionStorage(clean_db, 1, BOX, set)
    await stored.open()
    await stored.auth_key(b"k" * 256)
    await stored.user_id(EXPECTED)
    made: list[dict[str, Any]] = []

    def client(_name: str, **kwargs: Any) -> FakeClient:
        made.append(kwargs)
        return FakeClient(kwargs["storage_engine"])

    monkeypatch.setattr(pyrogram, "Client", client)
    await logout_offline(clean_db, BOX, _kurigram_config(), 1)
    assert made[0]["api_id"] == 12345 and made[0]["api_hash"] == "a" * 32


async def test_send_saved_uses_input_peer_self_through_fence() -> None:
    from pyrogram import raw

    t = FakeKurigram()
    await _online(t)

    await t.send_saved("привет в избранное")
    assert [name for name, _ in t.client.invoked[-1:]] == ["SendMessage"]
    last_query = t.client.queries[-1]
    assert isinstance(last_query.peer, raw.types.InputPeerSelf)
    assert last_query.message == "привет в избранное"

    # Через ограду аренды: если ограда просрочена — LeaseLost
    clock = FakeMonotonic()
    fence = Fence(1, 1, clock.now + 10.0, monotonic=clock)
    t_fenced = FakeKurigram(fence=fence)
    await _online(t_fenced)
    clock.now += 20.0
    with pytest.raises(LeaseLost):
        await t_fenced.send_saved("тест")


def test_saved_messages_not_accepted_by_chat_filter() -> None:
    chats = ChatsSection(
        game_chat_id=-1001234567,
        swinfo_chat_id=-1007654321,
        swinfo_user_id=123,
    )
    f = ChatFilter.from_settings(chats)
    own_user_id = 99999999
    saved_msg = NS(chat=NS(id=own_user_id), from_user=NS(id=own_user_id), reply_markup=None)
    assert not f.accepts(saved_msg)
