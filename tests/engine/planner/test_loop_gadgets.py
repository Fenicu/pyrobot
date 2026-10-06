"""Цикл и гаджеты: итоги сценариев — в `GadgetRuns.after`, `tick` на каждом шаге, паузы
`NOTHING_HOLD`."""

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import app.engine.planner.loop as loop_module
from app.engine.clock import SystemClock
from app.engine.gadgets import GadgetRuns
from app.engine.planner.loop import NOTHING_RETRY, PlannerLoop
from app.engine.planner.store import MemoryPlannerStore
from app.engine.planner.types import Act
from app.engine.scenarios.library import ScenarioResult
from app.engine.scenarios.registry import ScenarioSpec
from app.engine.state.model import BusyState, CharacterState, Obs
from tests.engine.fakegame import World, running_world
from tests.engine.planner.test_loop import QUIET, FixedClock, Notes

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async for w in running_world(QUIET):
        yield w


class Runs:
    """Запоминает вызовы `GadgetRuns`; `fail` — падать, как сбой учёта."""

    def __init__(self, fail: bool = False) -> None:
        self.ticks = 0
        self.results: list[tuple[str, dict[str, Any], ScenarioResult]] = []
        self.fail = fail

    async def tick(self) -> None:
        self.ticks += 1
        if self.fail:
            raise RuntimeError("tick")

    async def after(
        self, scenario: str, params: Mapping[str, Any], result: ScenarioResult
    ) -> None:
        self.results.append((scenario, dict(params), result))
        if self.fail:
            raise RuntimeError("after")


def loop(
    world: World,
    gadgets: Any,
    *,
    ready: str | None = None,
    auto: bool = True,
    clock: Any = None,
    notes: Notes | None = None,
) -> PlannerLoop:
    return PlannerLoop(
        gateway=world.gateway,
        state=lambda: world.state,
        settings=world.settings,
        clock=clock or SystemClock(),
        store=MemoryPlannerStore(),
        notifier=notes or Notes(),
        ready=lambda: ready,
        step_timeout_s=0.3,
        auto=auto,
        gadgets=gadgets,
    )


def scripted(monkeypatch: pytest.MonkeyPatch, scenario: str, result: ScenarioResult) -> None:
    async def fake(ctx: Any, state: Any, params: Any) -> ScenarioResult:
        return result

    monkeypatch.setitem(loop_module.SCENARIOS, scenario, ScenarioSpec(scenario, fake, True))


async def run(planner: PlannerLoop, scenario: str, params: dict[str, Any]) -> None:
    act = Act(scenario, params, "test")
    decision_id = await planner._store.record(datetime.now(UTC), act)
    await planner._execute(act, decision_id, dry_run=False)


@pytest.mark.parametrize(
    "result",
    [
        ScenarioResult("done", "bought", {"bought": "x", "price": 9, "rule": "empty"}),
        ScenarioResult("done", "batch", {"task_id": 3}),
        ScenarioResult("nothing", "shop_mismatch"),
        ScenarioResult("failed", "crashed"),
    ],
)
async def test_after_passes_results_to_gadget_runs(
    world: World, monkeypatch: pytest.MonkeyPatch, result: ScenarioResult
) -> None:
    runs = Runs()
    scripted(monkeypatch, "gadget_upgrade", result)
    await run(loop(world, runs), "gadget_upgrade", {"task_id": 3})
    assert runs.results == [("gadget_upgrade", {"task_id": 3}, result)]


async def test_after_failure_is_logged_and_run_closed(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = Runs(fail=True)
    scripted(monkeypatch, "gadget_buy", ScenarioResult("nothing", "cant_afford"))
    planner = loop(world, runs)
    await run(planner, "gadget_buy", {})
    assert len(runs.results) == 1
    assert planner._cooldowns["gadget_buy"] > datetime.now(UTC) + timedelta(minutes=59)
    store = planner._store
    assert isinstance(store, MemoryPlannerStore)
    assert [r.status for r in store.runs] == ["nothing"]


async def test_after_with_real_runs_notifies_purchase(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes = Notes()
    runs = GadgetRuns(
        settings=world.settings, state=lambda: world.state, notifier=notes, clock=SystemClock()
    )
    details = {"bought": "Шлёпки", "price": 79, "rule": "empty", "sold": []}
    scripted(monkeypatch, "gadget_buy", ScenarioResult("done", "bought", details))
    await run(loop(world, runs, notes=notes), "gadget_buy", {})
    assert notes.codes == ["gadget_bought"]


async def test_other_scenarios_do_not_reach_gadget_runs(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = Runs()
    scripted(monkeypatch, "book", ScenarioResult("done", "read"))
    await run(loop(world, runs), "book", {})
    assert runs.results == []


async def test_tick_runs_each_step(world: World) -> None:
    runs = Runs()
    await loop(world, runs, ready="tg_offline").step()
    assert runs.ticks == 1
    await loop(world, runs, auto=False)._idle()
    assert runs.ticks == 2
    # Сбой tick шаг не роняет.
    failing = Runs(fail=True)
    await loop(world, failing, ready="tg_offline").step()
    assert failing.ticks == 1


@pytest.mark.parametrize(
    ("reason", "hold"),
    [
        ("cant_afford", timedelta(hours=1)),
        ("market_closed", timedelta(minutes=30)),
        ("shop_mismatch", timedelta(hours=6)),
        ("dump_window", NOTHING_RETRY),
    ],
)
async def test_nothing_holds(
    world: World, monkeypatch: pytest.MonkeyPatch, reason: str, hold: timedelta
) -> None:
    scripted(monkeypatch, "gadget_buy", ScenarioResult("nothing", reason))
    planner = loop(world, Runs(), clock=FixedClock(NOW))
    await run(planner, "gadget_buy", {})
    assert planner._cooldowns["gadget_buy"] == NOW + hold


@pytest.mark.parametrize("scenario", ["gadget_buy", "gadget_wear_set", "gadget_upgrade"])
async def test_busy_holds_until_deed_ends(
    world: World, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    # Игра ответила «занят»: шаг гаджетов ждёт конца дела, а не повторяет каждую минуту.
    deed = BusyState(activity="job", until=NOW + timedelta(minutes=25))
    state = CharacterState(busy=Obs(value=deed, at=NOW))
    scripted(monkeypatch, scenario, ScenarioResult("refused", "busy"))
    planner = loop(world, Runs(), clock=FixedClock(NOW))
    planner._state = lambda: state
    await run(planner, scenario, {})
    assert planner._cooldowns[scenario] == deed.until
    # Конец дела неизвестен — общая минута.
    planner._state = CharacterState
    await run(planner, scenario, {})
    assert planner._cooldowns[scenario] == NOW + NOTHING_RETRY
