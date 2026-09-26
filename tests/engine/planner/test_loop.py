import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.engine.notify import Level
from app.engine.planner.loop import PlannerLoop
from app.engine.planner.store import MemoryPlannerStore
from app.engine.planner.types import Decision
from tests.engine.fakegame import LIVE, World, running_world


class ShiftClock:
    def __init__(self) -> None:
        self.shift = timedelta()

    def now(self) -> datetime:
        return datetime.now(UTC) + self.shift

    def monotonic(self) -> float:
        return time.monotonic()


class Notes:
    def __init__(self) -> None:
        self.codes: list[str] = []

    async def notify(self, level: Level, code: str, text: str) -> None:
        self.codes.append(code)


class Rig:
    def __init__(self, world: World, ready: str | None = None) -> None:
        self.world = world
        self.clock = ShiftClock()
        self.store = MemoryPlannerStore()
        self.notes = Notes()
        self.ready = ready
        self.loop = PlannerLoop(
            gateway=world.gateway,
            state=lambda: world.state,
            settings=world.settings,
            clock=self.clock,
            store=self.store,
            notifier=self.notes,
            ready=lambda: self.ready,
            step_timeout_s=0.3,
        )

    async def steps(self, n: int) -> None:
        for _ in range(n):
            await self.loop.step()


DRY = LIVE.model_copy(update={"engine": LIVE.engine.model_copy(update={"mode": "dry_run"})})


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async for w in running_world():
        yield w


@pytest.fixture
async def dry_world() -> AsyncIterator[World]:
    async for w in running_world(DRY):
        yield w


async def set_engine(world: World, **update: object) -> None:
    await world.settings.update(
        lambda s: s.model_copy(update={"engine": s.engine.model_copy(update=update)}),
        changed_by="test",
    )


def script_day(world: World) -> None:
    game = world.game
    game.on_text("😎Я", ("profile", 3624478))
    game.on_text("/inv", ("items", 3625102))
    game.on_text("/read_exp", ("items", 3516680))
    game.on_text("/to_eat", ("food", 3624997))
    game.on_text("/use_card", ("items", 3516678))
    game.on_text("/gifts", ("items", 3516682))
    game.on_text("/gorbushka", ("gorbushka", 3516741))
    game.on_text("/job", ("activities", 3623881))


async def test_from_empty_state_to_first_deed(world: World) -> None:
    script_day(world)
    rig = Rig(world)
    await rig.steps(9)
    assert world.game.payloads() == [
        "😎Я",
        "/inv",
        "/read_exp",
        "/to_eat",
        "/use_card",
        "/gifts",
        "/gorbushka",
        "/job",
    ]
    runs = [(r.scenario, r.status) for r in rig.store.runs]
    assert runs[-2:] == [("gorbushka", "nothing"), ("deed:job", "done")]
    last = rig.store.decisions[-1][1]
    assert (last.kind, last.reason) == ("wait", "gorbushka_next")
    gorbushka = world.state.gorbushka
    assert gorbushka is not None and rig.loop.next_wake == gorbushka.value.next_fight_at


async def test_repeated_wait_recorded_once(world: World) -> None:
    script_day(world)
    rig = Rig(world)
    await rig.steps(12)
    waits = [d for _, d in rig.store.decisions if d.kind == "wait"]
    assert len(waits) == 1


async def test_not_ready_does_not_decide(world: World) -> None:
    rig = Rig(world, ready="paused")
    assert await rig.loop.step() == 5.0
    assert rig.store.decisions == [] and world.game.payloads() == []


async def test_dry_run_defers_suppressed_and_decides_the_rest(dry_world: World) -> None:
    script_day(dry_world)
    rig = Rig(dry_world)
    await rig.steps(12)
    assert dry_world.game.payloads() == ["😎Я", "/inv", "/to_eat", "/gifts", "/gorbushka"]
    runs = [(r.scenario, r.status) for r in rig.store.runs if r.status == "suppressed"]
    assert runs == [("book", "suppressed"), ("card", "suppressed"), ("deed:job", "suppressed")]
    held = {name for name, until in rig.loop._cooldowns.items() if until > rig.clock.now()}
    assert {"book", "card", "deed:job", "deed:harvest"} <= held
    assert rig.store.decisions[-1][1].kind == "wait"


async def test_failures_cool_down_and_notify(world: World) -> None:
    rig = Rig(world)
    await rig.loop.step()
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("refresh", "failed", "timeout")
    ]
    await rig.loop.step()
    assert len(rig.store.runs) == 1
    assert rig.notes.codes == ["scenario_failed"]
    for _ in range(2):
        rig.clock.shift += timedelta(minutes=6)
        await rig.loop.step()
    assert len(rig.store.runs) == 3
    assert rig.notes.codes == ["scenario_failed"]


async def test_pause_between_steps_is_not_failure(world: World) -> None:
    world.game.on_text("😎Я", ("profile", 3624478))
    world.game.on_text("/to_eat", ("food", 3521844))
    await world.feed("profile", 3624478)
    await world.feed("items", 3625102)
    await world.feed("items", 3516682)
    await world.settings.update(
        lambda s: s.model_copy(
            update={
                "features": s.features.model_copy(
                    update={"books": False, "cards_containers": False}
                )
            }
        ),
        changed_by="test",
    )
    rig = Rig(world)
    await set_engine(world, paused=True)
    await world.feed("food", 3521844)
    await rig.loop.step()
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("fastfood", "stopped", "paused")
    ]
    assert rig.loop._cooldowns == {}


async def test_pause_after_decision_stops_first_step(world: World) -> None:
    await world.feed("profile", 3624478)
    await world.feed("items", 3625102)
    rig = Rig(world)
    record = rig.store.record

    async def pause_then_record(at: datetime, decision: Decision) -> int:
        await set_engine(world, paused=True)
        return await record(at, decision)

    rig.store.record = pause_then_record  # type: ignore[method-assign]
    await rig.loop.step()
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("book", "failed", "paused")
    ]
    assert world.game.payloads() == [] and rig.loop._cooldowns == {}
    assert rig.notes.codes == []


async def test_uncertified_step_stays_suppressed_after_switch_to_live(dry_world: World) -> None:
    dry_world.game.on_text("🛌Спать", ("sleep", 3526861))
    await dry_world.feed("profile", 3610633)
    send_text = dry_world.game.send_text

    async def switch_after_menu(chat_id: int, text: str, reply_to: int | None = None) -> int:
        sent = await send_text(chat_id, text, reply_to)
        if text == "🛌Спать":
            await set_engine(dry_world, mode="live")
        return sent

    dry_world.game.send_text = switch_after_menu  # type: ignore[method-assign]
    rig = Rig(dry_world)
    await rig.loop.step()
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("sleep", "suppressed", "uncertified")
    ]
    assert dry_world.game.payloads() == ["🛌Спать"]
