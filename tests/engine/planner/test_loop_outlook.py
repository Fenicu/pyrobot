import asyncio
import time
from datetime import date, datetime
from typing import Any

import pytest

from app.engine.metro.store import MemoryMetroRunStore
from app.engine.planner import loop as loop_module
from app.engine.planner.loop import LoopView, PlannerLoop
from app.engine.planner.store import DecisionRecord, MemoryPlannerStore
from app.engine.planner.types import Act, Wait
from app.engine.scenarios.library import ScenarioResult
from app.engine.scenarios.registry import ScenarioSpec
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


async def test_outlook_reads_empty_caches_without_filling_them() -> None:
    loop, store, metro = rig(awake())
    first = await loop.outlook()
    assert first.now == NOW
    # Пустые кеши план читает из хранилища, но не заполняет: их грузит и ведёт только step().
    assert store.reads == ["last_done", "done_on_day"] and metro.reads == 1
    assert (loop._last_done, loop._metro_durations, loop._done_today) == (None, None, None)
    assert store.decisions == [] and store.runs == []

    async def execute(act: Act, decision_id: int, *, dry_run: bool) -> None:
        pass

    loop._execute = execute  # type: ignore[method-assign]
    await loop.step()
    store.reads.clear()
    again = await loop.outlook()
    # Кеши есть — чтений БД нет.
    assert store.reads == [] and metro.reads == 2
    assert again.outlook.decision == first.outlook.decision


class FailingLastDone(CountingStore):
    """`last_done` ломается, пока тест не починит: план должен строиться и без него."""

    def __init__(self) -> None:
        super().__init__()
        self.broken = True

    async def last_done(self) -> dict[str, datetime]:
        self.reads.append("last_done")
        if self.broken:
            raise ConnectionError("db down")
        return await super(CountingStore, self).last_done()


async def test_outlook_survives_last_done_store_failure() -> None:
    """Как `_peek_today`: сбой хранилища не роняет план, значение — пустое."""
    loop, _, _ = rig(awake())
    store = FailingLastDone()
    loop._store = store
    view = await loop.outlook()
    assert isinstance(view.outlook.decision, Act | Wait)
    # Не закешировано в цикле: сбой не портит кеш step(), следующий проход перечитает.
    assert loop._last_done is None
    store.broken = False
    again = await loop.outlook()
    assert again.outlook.decision == view.outlook.decision


class SlowStore(CountingStore):
    """Первые чтения кешей ждут, пока тест их не отпустит: порядок завершения задаёт тест."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.slow = 1

    async def _maybe_wait(self) -> None:
        if self.slow > 0:
            self.slow -= 1
            await self.gate.wait()

    async def last_done(self) -> dict[str, datetime]:
        await self._maybe_wait()
        return await super().last_done()


async def test_late_outlook_read_does_not_overwrite_loop_caches() -> None:
    loop, _, _ = rig(awake())
    store = SlowStore()
    loop._store = store
    executed: list[Act] = []

    async def execute(act: Act, decision_id: int, *, dry_run: bool) -> None:
        executed.append(act)

    loop._execute = execute  # type: ignore[method-assign]
    # GET начал читать last_done и ждёт; тем временем цикл загрузил кеш и учёл успешное дело.
    pending = asyncio.create_task(loop.outlook())
    await asyncio.sleep(0)
    await loop.step()
    job = Act("deed:job", {}, "best")
    await loop._after(job, ScenarioResult("done", "activity_started"), NOW, NOW)
    assert loop._last_done == {"deed:job": NOW}
    store.gate.set()
    await pending
    # Позднее чтение плана кеш цикла не трогает.
    assert loop._last_done == {"deed:job": NOW}
    assert executed


async def test_running_only_while_loop_task_runs() -> None:
    loop, _, _ = rig(awake(), auto=False)
    assert not loop.running
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0)
    assert loop.running
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not loop.running


async def test_crashed_loop_is_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    loop, _, _ = rig(awake())

    async def crash() -> float | None:
        raise RuntimeError("boom")

    monkeypatch.setattr(loop, "step", crash)
    with pytest.raises(RuntimeError):
        await loop.run()
    assert not loop.running


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


async def test_loop_view_tells_what_the_loop_waits_for() -> None:
    loop, _, _ = rig(awake(motivation=0, motivation_next_at=m(20)))
    view = loop.loop_view()
    assert (view.wait_reason, view.wake_at) == (None, None)
    await loop.step()
    view = loop.loop_view()
    assert (view.wait_reason, view.next_wake, view.wake_at) == ("motivation", w(20), w(20))


async def test_long_wait_wakes_the_loop_after_max_idle() -> None:
    # Ожидание дольше max_idle_s: цикл проснётся раньше срока и решит заново.
    loop, _, _ = rig(awake(motivation=0, motivation_next_at=m(90)))
    await loop.step()
    view = loop.loop_view()
    assert (view.wait_reason, view.next_wake) == ("motivation", w(90))
    assert view.wake_at == m(30)


async def test_act_and_not_ready_clear_the_wait() -> None:
    state = {"now": awake(motivation=0)}
    ready: dict[str, str | None] = {"now": None}
    loop, _, _ = rig(state["now"])
    loop._state = lambda: state["now"]
    loop._ready = lambda: ready["now"]

    async def execute(act: Act, decision_id: int, *, dry_run: bool) -> None:
        pass

    loop._execute = execute  # type: ignore[method-assign]
    await loop.step()
    assert loop.loop_view().wait_reason == "motivation"
    state["now"] = awake()
    await loop.step()
    view = loop.loop_view()
    assert (view.wait_reason, view.next_wake, view.wake_at) == (None, None, None)
    state["now"] = awake(motivation=0)
    await loop.step()
    ready["now"] = "tg_offline"
    await loop.step()
    view = loop.loop_view()
    assert (view.wait_reason, view.next_wake, view.wake_at) == (None, None, None)


async def test_loop_view_tells_what_runs_with_its_params(monkeypatch: pytest.MonkeyPatch) -> None:
    # Идущий запуск — со своими параметрами (и зафиксированными в реестре): по одному имени
    # обновление инвентаря не отличить от обновления профиля.
    loop, store, _ = rig(awake())
    seen: list[LoopView] = []

    async def fake(ctx: Any, state: Any, params: Any) -> ScenarioResult:
        seen.append(loop.loop_view())
        return ScenarioResult("done", "ok")

    spec = ScenarioSpec("refresh", fake, True, {"fixed": 1})
    monkeypatch.setitem(loop_module.SCENARIOS, "refresh", spec)
    act = Act("refresh", {"source": "inventory"}, "state needs books")
    await loop._execute(act, await store.record(NOW, act), dry_run=False)
    await loop.request("refresh", {"source": "profile"}, key="k1", by="admin")
    await loop.run_manual()
    assert [(v.current, v.current_params) for v in seen] == [
        ("refresh", {"fixed": 1, "source": "inventory"}),
        ("refresh", {"fixed": 1, "source": "profile"}),
    ]
    view = loop.loop_view()
    assert (view.current, view.current_params) == (None, None)
