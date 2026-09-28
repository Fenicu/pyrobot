from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.engine.notify import Level
from app.engine.tg_auth import InvalidPhone, SendCodeRejected, TgAuthManager, TgState
from app.engine.transport.base import FloodWait, TransportAuthLost, TransportRejected
from tests.engine.helpers import GAME
from tests.engine.kurigram_fakes import EXPECTED, FakeKurigram, rpc_error


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


async def test_probe_unauthorized_resets_client_and_reports(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
    lost = await _online(t)
    t.client.errors["GetState"] = rpc_error("SessionRevoked")
    await t.probe()
    assert t.clients[0].invoked[-1] == (
        "GetState",
        {"retries": 1, "sleep_threshold": 0, "retry_delay": 0},
    )
    _assert_reset(t)
    assert lost == [1]


async def test_probe_other_error_only_logged(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
    lost = await _online(t)
    t.client.errors["GetState"] = OSError("network down")
    await t.probe()
    assert len(t.clients) == 1 and t.client.is_initialized and lost == []


@pytest.mark.parametrize("op", ["send", "click"])
async def test_send_click_unauthorized_resets_client(tmp_path: Path, op: str) -> None:
    t = FakeKurigram(tmp_path)
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


async def test_reset_survives_stop_failure(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
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


async def test_stop_forces_disconnect_on_failure(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
    await _online(t)
    client = t.client
    client.stop_error = RuntimeError("stop failed")
    await t.stop()
    assert not client.is_initialized and not client.is_connected
    assert client.storage.closed


async def test_failed_log_out_still_resets_client(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
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


async def test_log_out_of_revoked_session_is_success(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
    await _online(t)
    t.client.errors["LogOut"] = rpc_error("SessionRevoked")
    await t.log_out()
    _assert_reset(t)


async def test_relogin_after_loss_reaches_online(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
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


async def test_click_bot_response_timeout_is_no_toast(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
    lost = await _online(t)
    t.client.errors["GetBotCallbackAnswer"] = rpc_error("BotResponseTimeout")
    assert await t.click(GAME, 1, "maze_up", 1.0) is None
    assert len(t.clients) == 1 and lost == []


async def test_click_other_bad_request_rejected(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
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


async def test_send_code_invalid_phone_classified(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path, authorized=False)
    await t.connect()
    t.client.errors["SendCode"] = rpc_error("PhoneNumberInvalid")
    with pytest.raises(InvalidPhone):
        await t.send_code("+1")


async def test_send_code_flood_wait_mapped(tmp_path: Path) -> None:
    from pyrogram import errors

    t = FakeKurigram(tmp_path, authorized=False)
    await t.connect()
    t.client.errors["SendCode"] = errors.FloodWait(30)
    with pytest.raises(FloodWait) as info:
        await t.send_code("+1")
    assert info.value.seconds == 30


async def test_send_code_other_bad_request_rejected(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path, authorized=False)
    await t.connect()
    t.client.errors["SendCode"] = rpc_error("PhoneNumberBanned")
    with pytest.raises(SendCodeRejected) as info:
        await t.send_code("+1")
    assert info.value.code == "phone_number_banned"


async def test_identify_unauthorized_resets_client_without_callback(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
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


async def test_boot_revoked_session_reports_and_resets_without_deadlock(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
    rec = Recorder()
    mgr = TgAuthManager(t, expected_user_id=EXPECTED, notifier=rec)
    t.on_auth_lost = mgr.mark_lost
    t.client.errors["GetMe"] = rpc_error("SessionRevoked")
    st = await mgr.boot()
    assert st.state is TgState.UNAUTHORIZED and st.error == "session_revoked"
    assert rec.items == [("error", "tg_auth_lost")]


async def test_boot_calls_get_me_once(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
    mgr = TgAuthManager(t, expected_user_id=EXPECTED)
    assert (await mgr.boot()).state is TgState.ONLINE
    assert t.client.get_me_calls == 1
    assert t.client.me is not None and t.client.me.id == EXPECTED


async def test_fetch_converts_current_message(tmp_path: Path) -> None:
    from datetime import datetime

    t = FakeKurigram(tmp_path)
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


async def test_fetch_unauthorized_resets_client(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
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


async def test_forward_single_attempt_returns_destination_id(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
    await _online(t)
    t.client.responses["ForwardMessages"] = _forwarded(4242)
    assert await t.forward(GAME, 77, -1001149209877) == 4242
    name, kw = t.client.invoked[-1]
    assert name == "ForwardMessages"
    assert kw == {"retries": 1, "sleep_threshold": 0, "retry_delay": 0}


async def test_forward_without_id_in_answer_is_zero(tmp_path: Path) -> None:
    t = FakeKurigram(tmp_path)
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
async def test_forward_errors_classified(
    tmp_path: Path, error: str, raised: type[Exception]
) -> None:
    t = FakeKurigram(tmp_path)
    await _online(t)
    t.client.errors["ForwardMessages"] = rpc_error(error)
    with pytest.raises(raised):
        await t.forward(GAME, 77, -1001149209877)


async def test_forward_flood_wait_mapped(tmp_path: Path) -> None:
    from pyrogram import errors

    t = FakeKurigram(tmp_path)
    await _online(t)
    t.client.errors["ForwardMessages"] = errors.FloodWait(7)
    with pytest.raises(FloodWait):
        await t.forward(GAME, 77, -1001149209877)


@pytest.mark.parametrize("error", [KeyError("unknown peer"), OSError("network down")])
async def test_forward_unresolved_peer_is_refusal_not_unknown(
    tmp_path: Path, error: Exception
) -> None:
    # До ForwardMessages дело не дошло: пересылки точно нет — отказ, а не неясный исход.
    t = FakeKurigram(tmp_path)
    await _online(t)
    t.client.errors["ResolvePeer"] = error
    with pytest.raises(TransportRejected, match="peer"):
        await t.forward(GAME, 77, -1001149209877)
    assert all(name != "ForwardMessages" for name, _ in t.client.invoked)
