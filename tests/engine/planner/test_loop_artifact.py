from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.engine.artifact import ArtifactRuns, start
from app.engine.clock import SystemClock
from app.engine.planner.loop import PlannerLoop
from app.engine.planner.store import MemoryPlannerStore
from app.engine.settings import ArtifactRunSection
from app.engine.state.model import CharacterState
from tests.engine.artifact_texts import (
    SCREEN,
    SCREEN_COLLECTING,
    START_BUTTONS,
    START_LIGHT,
    STARTED_LIGHT,
    game_text,
)
from tests.engine.fakegame import World, running_world
from tests.engine.planner.test_loop import QUIET, Notes, script_day


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async for w in running_world(QUIET):
        yield w


def loop(
    world: World,
    notes: Notes,
    *,
    ready: str | None = None,
    auto: bool = True,
    store: MemoryPlannerStore | None = None,
) -> PlannerLoop:
    artifacts = ArtifactRuns(
        settings=world.settings, state=lambda: world.state, notifier=notes, clock=SystemClock()
    )
    return PlannerLoop(
        gateway=world.gateway,
        state=lambda: world.state,
        settings=world.settings,
        clock=SystemClock(),
        store=store or MemoryPlannerStore(),
        notifier=notes,
        ready=lambda: ready,
        step_timeout_s=0.3,
        auto=auto,
        artifacts=artifacts,
    )


async def test_first_step_closes_missed_collect_even_when_not_ready(world: World) -> None:
    now = datetime.now(UTC)
    run = ArtifactRunSection.model_validate(
        {
            "artifact": "light",
            "status": "active",
            "started_at": now - timedelta(days=11),
            "ends_at": now - timedelta(days=1),
        }
    )
    await world.settings.update(
        lambda s: s.model_copy(update={"artifact_run": run}), changed_by="test"
    )
    notes = Notes()
    await loop(world, notes, ready="tg_offline").step()
    assert world.settings.current.artifact_run.status == "finished"
    assert notes.codes == ["artifact_finished"]
    assert world.game.payloads() == []


async def test_start_run_through_scenario_activates_collect(world: World) -> None:
    now = datetime.now(UTC)
    await world.settings.update(
        lambda s: start(s, CharacterState(), "light", lottery_max=False, now=now),
        changed_by="test",
    )
    world.game.on_text("/artefacts", game_text(SCREEN))
    world.game.on_text("/artr_light", game_text(START_LIGHT, buttons=START_BUTTONS))
    world.game.on_click("artr_light_accept", new=(game_text(STARTED_LIGHT),))
    notes = Notes()
    planner = loop(world, notes, auto=False)
    await planner.request("artifact_start", {"artifact": "light"}, key="k", by="admin")
    await planner.run_manual()
    run = world.settings.current.artifact_run
    assert (run.status, run.deed_hint) == ("active", "walk")
    assert run.started_at is not None and run.ends_at == run.started_at + timedelta(days=10)
    assert notes.codes == ["artifact_started"]
    assert world.game.payloads() == ["/artefacts", "/artr_light", "artr_light_accept"]
    motivation = world.state.motivation
    assert motivation is not None and motivation.value == 0


async def requested_light(world: World) -> None:
    now = datetime.now(UTC)
    await world.settings.update(
        lambda s: start(s, CharacterState(), "light", lottery_max=False, now=now),
        changed_by="test",
    )
    await world.feed("profile", 3624478)
    script_day(world)
    world.game.on_text("/artr_light", game_text(START_LIGHT, buttons=START_BUTTONS))


def decided(store: MemoryPlannerStore) -> list[str | None]:
    return [d.scenario for _, d in store.decisions]


async def test_step_after_started_does_not_start_again(world: World) -> None:
    # Успешный запуск кулдауна не ставит: запись уже не «starting» — к следующему решению.
    await requested_light(world)
    world.game.on_text("/artefacts", game_text(SCREEN))
    world.game.on_click("artr_light_accept", new=(game_text(STARTED_LIGHT),))
    notes, store = Notes(), MemoryPlannerStore()
    planner = loop(world, notes, store=store)
    await planner.step()
    assert decided(store) == ["artifact_start"]
    assert world.settings.current.artifact_run.status == "active"
    await planner.step()
    assert decided(store)[1] != "artifact_start"
    assert [r.scenario for r in store.runs].count("artifact_start") == 1
    assert notes.codes == ["artifact_started"]


async def test_step_after_checked_start_does_not_start_again(world: World) -> None:
    # Ответа на «Стартуем!» нет, экран артефактов показал наш сбор: запись закрывает `tick`
    # первым делом следующего шага, до решения.
    await requested_light(world)
    world.game.on_text("/artefacts", game_text(SCREEN))
    world.game.on_text("/artefacts", game_text(SCREEN_COLLECTING))
    notes, store = Notes(), MemoryPlannerStore()
    planner = loop(world, notes, store=store)
    await planner.step()
    assert [(r.scenario, r.status, r.reason) for r in store.runs] == [
        ("artifact_start", "done", "started_checked")
    ]
    await planner.step()
    assert world.settings.current.artifact_run.status == "active"
    assert decided(store)[1] != "artifact_start"
    assert notes.codes == ["artifact_started"]
