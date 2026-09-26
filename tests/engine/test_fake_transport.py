import asyncio

import pytest

from app.engine.transport.base import FloodWait
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
