from datetime import UTC, datetime

from app.engine.bus import Delivery
from app.engine.events import AntiFlood, Event, Unrecognized
from app.engine.notify import Level
from app.engine.unrecognized import UnrecognizedWatch
from tests.engine.helpers import make_msg


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return self.t


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[Level, str]] = []

    async def notify(self, level: Level, code: str, text: str) -> None:
        self.calls.append((level, code))


def _delivery(*events: Event) -> Delivery:
    return Delivery(
        msg=make_msg("x"), events=events, state_version=1, journal_id=1, reactable=True
    )


async def test_spike_notified_once_per_window() -> None:
    clock, rec = Clock(), Recorder()
    watch = UnrecognizedWatch(rec, clock, threshold=3, window_s=600)
    for _ in range(2):
        await watch.on_delivery(_delivery(Unrecognized(first_line="a")))
    await watch.on_delivery(_delivery(AntiFlood()))
    assert rec.calls == []
    await watch.on_delivery(_delivery(Unrecognized(first_line="b")))
    assert rec.calls == [("warn", "unrecognized_spike")]
    for _ in range(5):
        await watch.on_delivery(_delivery(Unrecognized(first_line="c")))
    assert len(rec.calls) == 1
    clock.t = 601
    for _ in range(3):
        await watch.on_delivery(_delivery(Unrecognized(first_line="d")))
    assert len(rec.calls) == 2


async def test_old_hits_leave_window() -> None:
    clock, rec = Clock(), Recorder()
    watch = UnrecognizedWatch(rec, clock, threshold=3, window_s=600)
    await watch.on_delivery(_delivery(Unrecognized(first_line="a")))
    await watch.on_delivery(_delivery(Unrecognized(first_line="a")))
    clock.t = 700
    await watch.on_delivery(_delivery(Unrecognized(first_line="a")))
    assert rec.calls == []
