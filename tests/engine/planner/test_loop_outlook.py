import time
from datetime import date, datetime
from typing import Any

from app.engine.metro.store import MemoryMetroRunStore
from app.engine.planner.loop import PlannerLoop
from app.engine.planner.store import DecisionRecord, MemoryPlannerStore
from app.engine.planner.types import Act, Wait
from app.engine.settings import Settings, StaticSettings
from app.engine.state.model import CharacterState
from tests.engine.planner.test_decide import BASE, NOW, awake, config, m, w


class Clock:
    def now(self) -> datetime:
        return NOW

    def monotonic(self) -> float:
        return time.monotonic()


class CountingStore(MemoryPlannerStore):
    """Хранилище решений, считающее чтения кешей цикла."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []

    async def last_done(self) -> dict[str, datetime]:
        self.reads.append("last_done")
        return await super().last_done()

    async def done_on_day(self, day: date) -> dict[str, int]:
        self.reads.append("done_on_day")
        return await super().done_on_day(day)


class CountingMetro(MemoryMetroRunStore):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    async def durations(self, limit: int = 20) -> list[float]:
        self.reads += 1
        return await super().durations(limit)


class Notes:
    async def notify(self, level: str, code: str, text: str) -> None:
        pass


def rig(
    state: CharacterState,
    settings: Settings = BASE,
    *,
    ready: str | None = None,
    auto: bool = True,
) -> tuple[PlannerLoop, CountingStore, CountingMetro]:
    store, metro = CountingStore(), CountingMetro()
    loop = PlannerLoop(
        gateway=object(),  # type: ignore[arg-type]
        state=lambda: state,
        settings=StaticSettings(settings),
        clock=Clock(),
        store=store,
        notifier=Notes(),
        ready=lambda: ready,
        metro_store=metro,
        auto=auto,
    )
    return loop, store, metro


async def test_outlook_loads_caches_once_and_writes_nothing() -> None:
    loop, store, metro = rig(awake())
    first = await loop.outlook()
    started = time.perf_counter()
    again = await loop.outlook()
    elapsed = time.perf_counter() - started
    assert again.outlook == first.outlook
    # Кеши цикла — по одному чтению на весь процесс; сам проход — без БД и без записи.
    assert store.reads == ["last_done", "done_on_day"] and metro.reads == 1
    assert store.decisions == [] and store.runs == []
    assert first.now == NOW
    # Порядок величин для README: проход — доли миллисекунды; порог с запасом на медленный CI.
    assert elapsed < 0.1


async def test_outlook_decides_like_step() -> None:
    loop, store, _ = rig(awake(), config({"strategy": {"deeds": ["job"]}}))
    loop._cooldowns["deed:job"] = m(30)
    view = await loop.outlook()
    assert view.outlook.decision == Wait(w(30), "cooldown:deed:job", view.outlook.considered)
    assert [(c.scenario, c.verdict) for c in view.outlook.considered] == [("deed:job", "cooldown")]
    await loop.step()
    assert store.decisions[-1][1] == DecisionRecord.of(view.outlook.decision)


async def test_outlook_act_matches_recorded_step_decision() -> None:
    loop, store, _ = rig(awake())
    executed: list[Act] = []

    async def execute(act: Act, decision_id: int, *, dry_run: bool) -> None:
        executed.append(act)

    loop._execute = execute  # type: ignore[method-assign]
    view = await loop.outlook()
    await loop.step()
    assert executed == [view.outlook.decision]
    assert store.decisions[-1][1] == DecisionRecord.of(view.outlook.decision)


async def test_outlook_drops_suppression_holds_after_mode_change() -> None:
    loop, store, _ = rig(awake(), config({"strategy": {"deeds": ["job"]}}))
    loop._held["deed:job"] = m(30)
    loop._mode = "live"

    async def execute(act: Act, decision_id: int, *, dry_run: bool) -> None:
        pass

    loop._execute = execute  # type: ignore[method-assign]
    view = await loop.outlook()
    assert isinstance(view.outlook.decision, Act)
    await loop.step()
    assert store.decisions[-1][1] == DecisionRecord.of(view.outlook.decision)


async def test_outlook_decides_even_when_loop_would_not() -> None:
    paused = BASE.model_copy(update={"engine": BASE.engine.model_copy(update={"paused": True})})
    loop, _, _ = rig(awake(), paused, ready="paused", auto=False)
    loop.current = "book"
    loop.next_wake = m(5)
    await loop.request("book", {}, key="k1", by="admin")
    view = await loop.outlook()
    assert view.loop.paused and view.loop.ready == "paused" and not view.loop.auto
    assert (view.loop.current, view.loop.manual_queue, view.loop.next_wake) == ("book", 1, m(5))
    # Решение условное: показывается, хотя цикл сейчас его не исполнит.
    assert isinstance(view.outlook.decision, Act)


async def test_revision_marks_loop_changes() -> None:
    loop, _, _ = rig(awake(motivation=0))
    marks: list[Any] = [loop.revision]
    await loop.step()
    marks.append(loop.revision)
    await loop.request("book", {}, key="k1", by="admin")
    marks.append(loop.revision)
    assert marks == sorted(set(marks))
