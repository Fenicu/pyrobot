import asyncio

import pytest

from app.engine.fence import Fence, LeaseLost


class FakeMonotonic:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_alive_until_deadline_and_extend_same_epoch_only() -> None:
    clock = FakeMonotonic()
    fence = Fence(1, 7, 110.0, monotonic=clock)
    assert (fence.account_id, fence.epoch, fence.deadline) == (1, 7, 110.0)
    clock.now = 109.9
    assert fence.alive
    fence.check()
    # Ответ продления по чужой эпохе срок не сдвигает.
    fence.extend(8, 130.0)
    assert (fence.epoch, fence.deadline) == (7, 110.0)
    fence.extend(7, 120.0)
    assert fence.deadline == 120.0
    clock.now = 115.0
    assert fence.alive
    clock.now = 120.0
    assert not fence.alive
    # Срок наступил — продление ограду не оживляет.
    fence.extend(7, 140.0)
    assert not fence.alive
    with pytest.raises(LeaseLost):
        fence.check()

    revoked = Fence(1, 7, 110.0, monotonic=FakeMonotonic())
    revoked.revoke()
    revoked.extend(7, 130.0)
    assert not revoked.alive and revoked.deadline == 110.0


def test_on_lost_called_once() -> None:
    lost: list[str] = []
    fence = Fence(1, 7, 110.0, monotonic=FakeMonotonic())
    fence.on_lost = lambda: lost.append("revoked")
    fence.revoke()
    fence.revoke()
    with pytest.raises(LeaseLost):
        fence.check()
    assert lost == ["revoked"]

    clock = FakeMonotonic(110.0)
    expired = Fence(1, 7, 110.0, monotonic=clock)
    expired.on_lost = lambda: lost.append("expired")
    for _ in range(2):
        with pytest.raises(LeaseLost):
            expired.check()
    expired.revoke()
    assert lost == ["revoked", "expired"]


async def test_call_refused_after_deadline() -> None:
    called: list[int] = []

    async def fn() -> int:
        called.append(1)
        return 1

    fence = Fence(1, 7, 110.0, monotonic=FakeMonotonic(110.0))
    with pytest.raises(LeaseLost):
        await fence.call(fn)
    assert called == []


async def test_call_cut_at_deadline() -> None:
    lost: list[int] = []
    cancelled: list[bool] = []
    clock = FakeMonotonic()
    fence = Fence(1, 7, clock.now + 0.05, monotonic=clock)
    fence.on_lost = lambda: lost.append(1)

    async def invoke() -> None:
        # Как invoke kurigram, ждущий запуска сессии до 15 с.
        try:
            await asyncio.sleep(15)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    with pytest.raises(LeaseLost):
        await asyncio.wait_for(fence.call(invoke), 2)
    assert cancelled == [True] and lost == [1]
    assert not fence.alive


async def test_call_own_timeout_passes_through() -> None:
    lost: list[int] = []
    fence = Fence(1, 7, 110.0, monotonic=FakeMonotonic())
    fence.on_lost = lambda: lost.append(1)

    async def fn() -> None:
        raise TimeoutError("свой срок вызова")

    with pytest.raises(TimeoutError, match="свой срок вызова"):
        await fence.call(fn)
    assert fence.alive and lost == []


async def test_call_follows_extended_deadline() -> None:
    clock = FakeMonotonic()
    fence = Fence(1, 7, clock.now + 0.05, monotonic=clock)

    async def fn() -> str:
        await asyncio.sleep(0.02)
        # Продление во время вызова: вызов обрезается уже по новому сроку.
        fence.extend(7, clock.now + 10)
        await asyncio.sleep(0.1)
        return "ok"

    assert await fence.call(fn) == "ok"
    assert fence.alive
