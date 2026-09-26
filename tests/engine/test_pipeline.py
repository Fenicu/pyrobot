import asyncio
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from app.engine.bus import Bus, Delivery
from app.engine.events import AntiFlood, Event
from app.engine.memory import MemoryJournal
from app.engine.parsing import default_parser
from app.engine.pipeline import NullReducer, Pipeline
from app.engine.types import IncomingMessage
from tests.engine.helpers import GAME, make_msg, now


class CountingReducer:
    def apply(
        self, state: dict[str, Any], msg: IncomingMessage, events: Sequence[Event]
    ) -> dict[str, Any]:
        if not events:
            return state
        return {**state, "events": state.get("events", 0) + len(events)}


def _pipeline(reducer: Any = None, journal: Any = None) -> tuple[Pipeline, list[Delivery], Any]:
    bus = Bus()
    seen: list[Delivery] = []

    async def collect(d: Delivery) -> None:
        seen.append(d)

    bus.subscribe(collect)
    journal = journal or MemoryJournal()
    pipe = Pipeline(
        journal=journal,
        parser=default_parser(),
        reducer=reducer or NullReducer(),
        bus=bus,
        retry_base_s=0.01,
    )
    return pipe, seen, journal


async def test_duplicate_revision_dropped() -> None:
    pipe, seen, _ = _pipeline()
    m = make_msg("Офис")
    assert await pipe.process(m) is not None
    assert await pipe.process(m) is None
    assert len(seen) == 1


async def test_state_version_bumps_only_on_change() -> None:
    pipe, seen, journal = _pipeline(CountingReducer())
    await pipe.process(make_msg("просто текст", msg_id=1))
    assert pipe.version == 0
    await pipe.process(make_msg("Ты шлёшь запросы к боту слишком часто.", msg_id=2))
    assert pipe.version == 1
    assert pipe.state == {"events": 1}
    assert seen[-1].events == (AntiFlood(),)
    assert seen[-1].state_version == 1
    assert journal.snapshot == ({"events": 1}, 1)


async def test_load_restores_state_and_version() -> None:
    journal = MemoryJournal()
    journal.snapshot = ({"events": 3}, 7)
    pipe, _, journal = _pipeline(CountingReducer(), journal=journal)
    await pipe.load()
    assert pipe.state == {"events": 3}
    assert pipe.version == 7
    await pipe.process(make_msg("Ты шлёшь запросы к боту слишком часто.", msg_id=1))
    assert pipe.version == 8
    assert pipe.state == {"events": 4}
    assert journal.snapshot == ({"events": 4}, 8)


async def test_journal_ids_increase() -> None:
    pipe, seen, _ = _pipeline()
    await pipe.process(make_msg("a", msg_id=1))
    await pipe.process(make_msg("b", msg_id=2))
    assert [d.journal_id for d in seen] == [1, 2]
    assert pipe.last_journal_id == 2


async def test_latest_is_monotonic_by_revision() -> None:
    pipe, _, _ = _pipeline()
    await pipe.process(make_msg("v200", msg_id=5, kind="edit", revision=200))
    await pipe.process(make_msg("v100", msg_id=5, kind="edit", revision=100))
    latest = pipe.latest(GAME, 5)
    assert latest is not None and latest.text == "v200"
    await pipe.process(make_msg("v200b", msg_id=5, kind="edit", revision=200))
    latest = pipe.latest(GAME, 5)
    assert latest is not None and latest.text == "v200b"


async def test_old_recovered_not_reactable() -> None:
    pipe, seen, _ = _pipeline()
    fresh = make_msg("x", msg_id=1, recovered=True, date=now() - timedelta(minutes=2))
    stale = make_msg("y", msg_id=2, recovered=True, date=now() - timedelta(hours=2))
    await pipe.process(fresh)
    await pipe.process(stale)
    assert [d.reactable for d in seen] == [True, False]


async def test_published_after_journal_append() -> None:
    order: list[str] = []
    bus = Bus()

    async def sub(d: Delivery) -> None:
        order.append(f"publish:{d.journal_id}")

    bus.subscribe(sub)

    class RecordingJournal(MemoryJournal):
        async def append(self, *args: Any, **kwargs: Any) -> int | None:
            result = await super().append(*args, **kwargs)
            order.append(f"append:{result}")
            return result

    pipe = Pipeline(
        journal=RecordingJournal(), parser=default_parser(), reducer=NullReducer(), bus=bus
    )
    await pipe.process(make_msg("x"))
    assert order == ["append:1", "publish:1"]


async def test_reducer_error_keeps_message() -> None:
    class Broken:
        def apply(self, state: Any, msg: Any, events: Any) -> Any:
            raise RuntimeError("bug")

    pipe, seen, journal = _pipeline(Broken())
    assert await pipe.process(make_msg("x")) is not None
    assert len(journal.rows) == 1 and pipe.version == 0 and len(seen) == 1


async def test_journal_failure_retried_in_order() -> None:
    class Flaky(MemoryJournal):
        def __init__(self) -> None:
            super().__init__()
            self.failures = 2

        async def append(self, *args: Any, **kwargs: Any) -> int | None:
            if self.failures:
                self.failures -= 1
                raise ConnectionError("db down")
            return await super().append(*args, **kwargs)

    pipe, seen, _journal = _pipeline(journal=Flaky())
    task = asyncio.create_task(pipe.run())
    try:
        await pipe.submit(make_msg("first", msg_id=1))
        await pipe.submit(make_msg("second", msg_id=2))
        deadline = asyncio.get_running_loop().time() + 2
        while len(seen) < 2 and asyncio.get_running_loop().time() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
        assert [d.msg.text for d in seen] == ["first", "second"]
        assert pipe.healthy
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_slow_or_failing_subscriber_isolated() -> None:
    bus = Bus(subscriber_timeout_s=0.05)
    hits: list[int] = []

    async def bad(d: Delivery) -> None:
        raise RuntimeError("boom")

    async def slow(d: Delivery) -> None:
        await asyncio.sleep(10)

    async def good(d: Delivery) -> None:
        hits.append(d.journal_id)

    bus.subscribe(bad, priority=0)
    bus.subscribe(slow, priority=5)
    bus.subscribe(good, priority=10)
    pipe = Pipeline(
        journal=MemoryJournal(), parser=default_parser(), reducer=NullReducer(), bus=bus
    )
    await asyncio.wait_for(pipe.process(make_msg("x")), 1)
    assert hits == [1]
