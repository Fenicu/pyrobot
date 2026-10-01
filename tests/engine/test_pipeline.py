import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from app.engine.bus import Bus, Delivery
from app.engine.events import AntiFlood, Event
from app.engine.fence import LeaseLost
from app.engine.memory import MemoryJournal
from app.engine.parsing import default_parser
from app.engine.pipeline import PIPELINE_QUEUE_MAX, NullReducer, Pipeline
from app.engine.state.ledger import Effect
from app.engine.types import IncomingMessage
from tests.engine.helpers import GAME, make_msg, now


class CountingReducer:
    def reduce(
        self, state: dict[str, Any], msg: IncomingMessage, events: Sequence[Event]
    ) -> tuple[dict[str, Any], tuple[Effect, ...]]:
        if not events:
            return state, ()
        return {**state, "events": state.get("events", 0) + len(events)}, ()


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
        def reduce(self, state: Any, msg: Any, events: Any) -> Any:
            raise RuntimeError("bug")

    pipe, seen, journal = _pipeline(Broken())
    assert await pipe.process(make_msg("x")) is not None
    assert len(journal.rows) == 1 and pipe.version == 0 and len(seen) == 1


async def test_metrics_error_keeps_message() -> None:
    def broken_metrics(old: dict[str, Any], new: dict[str, Any]) -> dict[str, float]:
        raise RuntimeError("bug")

    journal = MemoryJournal()
    pipe = Pipeline(
        journal=journal,
        parser=default_parser(),
        reducer=CountingReducer(),
        bus=Bus(),
        metrics=broken_metrics,
    )
    delivery = await pipe.process(make_msg("Ты шлёшь запросы к боту слишком часто.", msg_id=1))
    assert delivery is not None
    assert len(journal.rows) == 1
    assert journal.snapshot == ({"events": 1}, 1)
    assert journal.metrics == []


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


async def test_append_lease_lost_not_retried() -> None:
    class Fenced(MemoryJournal):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def append(self, *args: Any, **kwargs: Any) -> int | None:
            self.attempts += 1
            raise LeaseLost("account 1 lease (epoch 7) lost")

    journal = Fenced()
    journal.snapshot = ({"events": 3}, 7)
    pipe, seen, _ = _pipeline(CountingReducer(), journal=journal)
    await pipe.load()
    msg = make_msg("Ты шлёшь запросы к боту слишком часто.", msg_id=1)
    with pytest.raises(LeaseLost):
        await asyncio.wait_for(pipe.process(msg), 1)
    assert journal.attempts == 1
    assert (pipe.state, pipe.version) == ({"events": 3}, 7)
    assert pipe.latest(GAME, 1) is None and pipe.last_journal_id == 0 and seen == []
    assert pipe.healthy


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


async def test_drain_waits_for_queued_messages() -> None:
    class SlowJournal(MemoryJournal):
        async def append(self, *args: Any, **kwargs: Any) -> int | None:
            await asyncio.sleep(0.005)
            return await super().append(*args, **kwargs)

    pipe, seen, journal = _pipeline(journal=SlowJournal())
    task = asyncio.create_task(pipe.run())
    try:
        for i in range(10):
            await pipe.submit(make_msg(f"m{i}", msg_id=i))
        assert await pipe.drain(2.0) is True
        assert len(journal.rows) == 10 and len(seen) == 10
        assert pipe.unfinished == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_drain_times_out_on_hung_journal() -> None:
    class HungJournal(MemoryJournal):
        async def append(self, *args: Any, **kwargs: Any) -> int | None:
            await asyncio.Event().wait()
            return None

    pipe, _, _ = _pipeline(journal=HungJournal())
    task = asyncio.create_task(pipe.run())
    try:
        await pipe.submit(make_msg("a", msg_id=1))
        await pipe.submit(make_msg("b", msg_id=2))
        assert await pipe.drain(0.05) is False
        assert pipe.unfinished == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_pipeline_submit_waits_when_full() -> None:
    # Очередь конвейера ограничена: при заполнении обработчик kurigram ждёт места.
    gate = asyncio.Event()

    class GatedJournal(MemoryJournal):
        async def append(self, *args: Any, **kwargs: Any) -> int | None:
            await gate.wait()
            return await super().append(*args, **kwargs)

    pipe, _, journal = _pipeline(journal=GatedJournal())
    for i in range(PIPELINE_QUEUE_MAX):
        await pipe.submit(make_msg("x", msg_id=i))
    assert pipe.backlog() == PIPELINE_QUEUE_MAX
    late = asyncio.create_task(pipe.submit(make_msg("late", msg_id=PIPELINE_QUEUE_MAX)))
    await asyncio.sleep(0.01)
    assert not late.done()
    # Конвейер взял первое сообщение (запись в журнал ещё идёт) — место есть.
    task = asyncio.create_task(pipe.run())
    try:
        await asyncio.wait_for(late, 1.0)
        assert pipe.backlog() == PIPELINE_QUEUE_MAX and journal.rows == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_subscribe_during_publish_applies_to_next_delivery() -> None:
    bus = Bus()
    calls: list[str] = []

    async def late(d: Delivery) -> None:
        calls.append(f"late:{d.journal_id}")

    async def early(d: Delivery) -> None:
        calls.append(f"early:{d.journal_id}")
        if d.journal_id == 1:
            bus.subscribe(late, priority=200)

    bus.subscribe(early, priority=0)
    pipe = Pipeline(
        journal=MemoryJournal(), parser=default_parser(), reducer=NullReducer(), bus=bus
    )
    await pipe.process(make_msg("a", msg_id=1))
    await pipe.process(make_msg("b", msg_id=2))
    assert calls == ["early:1", "early:2", "late:2"]


async def test_metrics_and_unrecognized_reach_journal() -> None:
    from app.engine.settings import ChatsSection
    from app.engine.state.reducer import StateReducer
    from tests.fixtures import game_msg

    journal = MemoryJournal()
    reducer = StateReducer()
    pipe = Pipeline(
        journal=journal,
        parser=default_parser(ChatsSection()),
        reducer=reducer,
        bus=Bus(),
        metrics=reducer.metrics,
    )
    profile = game_msg("profile", 3624478)
    await pipe.process(profile)
    assert (profile.date, "money", 867.0) in journal.metrics
    count = len(journal.metrics)
    await pipe.process(make_msg("совсем непонятное", msg_id=77))
    assert len(journal.metrics) == count
    assert journal.unrecognized == [(2, "совсем непонятное")]


async def test_latest_evicted_over_capacity() -> None:
    pipe = Pipeline(
        journal=MemoryJournal(),
        parser=default_parser(),
        reducer=NullReducer(),
        bus=Bus(),
        latest_capacity=2,
    )
    for i in (1, 2, 3):
        await pipe.process(make_msg(f"m{i}", msg_id=i))
    assert pipe.latest(GAME, 1) is None
    assert pipe.latest(GAME, 2) is not None and pipe.latest(GAME, 3) is not None
    await pipe.process(make_msg("m2 edit", msg_id=2, kind="edit", revision=5))
    await pipe.process(make_msg("m4", msg_id=4))
    assert pipe.latest(GAME, 3) is None and pipe.latest(GAME, 2) is not None


async def test_prime_restores_latest_after_restart() -> None:
    pipe, seen, _ = _pipeline()
    old = make_msg("карта", msg_id=7, kind="edit", revision=300)
    pipe.prime(old)
    assert pipe.latest(GAME, 7) == old and seen == []
    pipe.prime(make_msg("старее", msg_id=7, kind="edit", revision=100))
    assert pipe.latest(GAME, 7) == old


async def test_memory_journal_revisions() -> None:
    journal = MemoryJournal()
    for msg in (make_msg("a", msg_id=3), make_msg("b", msg_id=3, kind="edit", revision=2)):
        await journal.append(msg, [], None, 0)
    await journal.append(make_msg("c", msg_id=4), [], None, 0)
    assert [m.text for m in await journal.revisions(GAME, 3)] == ["a", "b"]


async def test_effects_reach_journal_once() -> None:
    from app.engine.settings import ChatsSection
    from app.engine.state.reducer import StateReducer
    from tests.fixtures import game_msg

    journal = MemoryJournal()
    pipe = Pipeline(
        journal=journal, parser=default_parser(ChatsSection()), reducer=StateReducer(), bus=Bus()
    )
    book = game_msg("items", 3516680)
    await pipe.process(book)
    await pipe.process(book)
    edit = replace(book, revision=book.revision + 1, kind="edit")
    await pipe.process(edit)
    assert [(m.msg_id, e.kind, e.amounts) for m, e, _ in journal.ledger] == [
        (book.msg_id, "book", {"exp": 457})
    ]


async def lottery_edits_in_one_second(journal: Any) -> None:
    """Экран лотереи и две покупки билета за 💵 правками одной секунды через конвейер."""
    from app.engine.settings import ChatsSection
    from app.engine.state.reducer import StateReducer
    from tests.fixtures import game_msg, game_versions

    pipe = Pipeline(
        journal=journal, parser=default_parser(ChatsSection()), reducer=StateReducer(), bus=Bus()
    )
    await pipe.process(game_msg("lottery", 3626217))
    opened, clicked = game_versions("lottery", 3626219)
    await pipe.process(opened)
    await pipe.process(clicked)
    second = replace(clicked, text=(clicked.text or "").replace("1 из 10", "2 из 10"))
    assert (second.revision, second.date) == (clicked.revision, clicked.date)
    await pipe.process(second)


async def test_two_edits_in_one_second_both_in_ledger() -> None:
    journal = MemoryJournal()
    await lottery_edits_in_one_second(journal)
    assert [(e.kind, e.amounts) for _, e, _ in journal.ledger] == [
        ("lottery_tickets", {"money": -30})
    ] * 2


async def factory_report_again_after_horizon(journal: Any) -> None:
    """Отчёт о фабрике за 09.09 (/fb 12.09) и тот же отчёт через 15 дней — ключ `applied`
    редьюсера (14 дней) уже забыт."""
    from app.engine.settings import ChatsSection
    from app.engine.state.reducer import OUTCOME_HORIZON, StateReducer
    from tests.fixtures import game_msg

    pipe = Pipeline(
        journal=journal, parser=default_parser(ChatsSection()), reducer=StateReducer(), bus=Bus()
    )
    report = game_msg("crew", 3620025)
    await pipe.process(report)
    later = report.date + OUTCOME_HORIZON + timedelta(days=1)
    # Любое сообщение после горизонта: редьюсер чистит ключи `applied` старше 14 дней.
    other = game_msg("items", 3516680)
    await pipe.process(replace(other, date=later, created_at=later))
    await pipe.process(replace(report, msg_id=report.msg_id + 9000, date=later, created_at=later))


async def test_factory_report_once_after_reducer_forgets_it() -> None:
    journal = MemoryJournal()
    await factory_report_again_after_horizon(journal)
    assert [(e.kind, e.key) for _, e, _ in journal.ledger] == [
        ("factory", "factory:2026-09-09"),
        ("book", None),
    ]


async def test_commit_uncertain_then_conflict_reloads_snapshot() -> None:
    """Фиксация прошла, но ответ потерялся: повтор упирается в уже записанную ревизию — снимок и
    эффекты уже в журнале, состояние перечитывается из него, эффекты не задваиваются."""
    from app.engine.settings import ChatsSection
    from app.engine.state.reducer import StateReducer
    from tests.fixtures import game_msg

    class LostCommit(MemoryJournal):
        def __init__(self) -> None:
            super().__init__()
            self.lose = 1

        async def append(self, *args: Any, **kwargs: Any) -> int | None:
            result = await super().append(*args, **kwargs)
            if self.lose:
                self.lose -= 1
                raise ConnectionError("commit result lost")
            return result

    journal = LostCommit()
    seen: list[Delivery] = []
    bus = Bus()

    async def collect(d: Delivery) -> None:
        seen.append(d)

    bus.subscribe(collect)
    pipe = Pipeline(
        journal=journal,
        parser=default_parser(ChatsSection()),
        reducer=StateReducer(),
        bus=bus,
        retry_base_s=0.01,
    )
    book = game_msg("items", 3516680)
    assert await pipe.process(book) is None
    assert (pipe.state, pipe.version) == journal.snapshot and pipe.version == 1
    assert pipe.healthy and seen == []
    assert len(journal.ledger) == 1
    await pipe.process(book)
    assert len(journal.ledger) == 1
