import asyncio

import pytest

from app.engine.transport.base import FloodWait, Sender, TransportRejected
from app.engine.transport.fake import FakeTransport, Sent


async def test_records_hooks_and_responds() -> None:
    t = FakeTransport()
    inside: list[str] = []
    got: list[Sent] = []

    async def responder(rec: Sent) -> None:
        got.append(rec)

    t.before_send = lambda rec: inside.append(rec.payload)
    t.responder = responder
    assert await t.send_text(1, "/stock") > 0
    assert inside == ["/stock"] and got == []
    t.toast = "Куплен баф"
    assert await t.click(1, 5, "maze_up", 1.0) == "Куплен баф"
    await asyncio.sleep(0)
    assert [r.payload for r in t.sent] == ["/stock", "maze_up"]
    assert [r.payload for r in got] == ["/stock", "maze_up"]


async def test_fail_with_queue() -> None:
    t = FakeTransport()
    t.fail_with.append(FloodWait(3))
    with pytest.raises(FloodWait) as exc:
        await t.send_text(1, "x")
    assert exc.value.seconds == 3
    assert t.sent == []
    assert await t.send_text(1, "y") > 0


async def test_chat_message_returns_new_id_and_is_recorded() -> None:
    t = FakeTransport()
    first = await t.send_chat_message(-1001377961602, "🍊")
    second = await t.send_chat_message(-1001377961602, "ещё")
    assert second > first > 0
    assert t.posted == [(-1001377961602, "🍊"), (-1001377961602, "ещё")]
    assert t.sent == []
    t.fail_with.append(FloodWait(3))
    with pytest.raises(FloodWait):
        await t.send_chat_message(-1001377961602, "🍊")
    assert len(t.posted) == 2


async def test_join_chat_with_other_id_is_mismatch_without_join() -> None:
    t = FakeTransport()
    t.chat_ids["mandarinkaSW"] = -1001
    with pytest.raises(TransportRejected, match="chat_mismatch"):
        await t.join_chat("mandarinkaSW", -1001377961602)
    assert t.joins == []
    assert await t.join_chat("mandarinkaSW", -1001) == "joined"


async def test_message_sender_from_table_and_recorded() -> None:
    t = FakeTransport()
    t.senders[(-1001377961602, 7)] = Sender(42, "Анна", "К", "anna")
    assert await t.message_sender(-1001377961602, 7) == Sender(42, "Анна", "К", "anna")
    assert await t.message_sender(-1001377961602, 8) is None
    t.sender_fail_with.append(FloodWait(3))
    with pytest.raises(FloodWait):
        await t.message_sender(-1001377961602, 7)
    assert t.sender_lookups == [(-1001377961602, 7), (-1001377961602, 8), (-1001377961602, 7)]


async def test_inline_recorded_with_new_id() -> None:
    t = FakeTransport()
    first = await t.send_inline(227859379, -100500, "join_fight_GXnJJ0QNK2K")
    assert first > 0
    [sent] = t.sent
    assert (sent.kind, sent.chat_id, sent.payload) == ("inline", -100500, "join_fight_GXnJJ0QNK2K")
