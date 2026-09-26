import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.engine.notify import Level
from app.engine.planner.decide import TIMER_MARGIN
from app.engine.planner.loop import DEEDS, MAX_RETRY, RETRY_AFTER, PlannerLoop
from app.engine.planner.store import MemoryPlannerStore
from app.engine.planner.types import Act, Decision
from app.engine.scenarios.library import ScenarioResult
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
    assert gorbushka is not None and gorbushka.value.next_fight_at is not None
    assert rig.loop.next_wake == gorbushka.value.next_fight_at + TIMER_MARGIN


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


async def test_not_ready_clears_next_wake(world: World) -> None:
    script_day(world)
    rig = Rig(world)
    await rig.steps(9)
    assert rig.loop.next_wake is not None
    rig.ready = "paused"
    await rig.loop.step()
    assert rig.loop.next_wake is None


async def test_dry_run_defers_suppressed_and_decides_the_rest(dry_world: World) -> None:
    script_day(dry_world)
    rig = Rig(dry_world)
    await rig.steps(12)
    assert dry_world.game.payloads() == ["😎Я", "/inv", "/to_eat", "/gifts", "/gorbushka"]
    runs = [(r.scenario, r.status) for r in rig.store.runs if r.status == "suppressed"]
    assert runs == [("book", "suppressed"), ("card", "suppressed"), ("deed:job", "suppressed")]
    held = {name for name, until in rig.loop._held.items() if until > rig.clock.now()}
    assert {"book", "card", "deed:job", "deed:harvest"} <= held
    assert rig.store.decisions[-1][1].kind == "wait"


async def test_switch_to_live_lifts_dry_run_holds(dry_world: World) -> None:
    script_day(dry_world)
    rig = Rig(dry_world)
    await rig.steps(12)
    assert "book" in rig.loop._held
    await set_engine(dry_world, mode="live")
    await rig.loop.step()
    assert dry_world.game.payloads()[-1] == "/read_exp"
    assert rig.store.runs[-1].status == "done"


async def test_kill_switch_suppression_is_not_held(world: World) -> None:
    await world.feed("profile", 3624478)
    await world.feed("items", 3625102)
    await world.gateway.kill("test")
    rig = Rig(world)
    await rig.loop.step()
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("book", "suppressed", "kill_switch")
    ]
    assert rig.loop._held == {} and rig.loop._cooldowns == {}


async def test_failures_cool_down_and_notify(world: World) -> None:
    rig = Rig(world)
    await rig.loop.step()
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("refresh", "failed", "timeout")
    ]
    await rig.loop.step()
    assert len(rig.store.runs) == 1
    assert rig.notes.codes == ["scenario_failed"]
    # Серия неудач: 5 мин, затем 10.
    for shift, runs in ((6, 2), (6, 2), (5, 3)):
        rig.clock.shift += timedelta(minutes=shift)
        await rig.loop.step()
        assert len(rig.store.runs) == runs
    assert rig.notes.codes == ["scenario_failed"]


def moment() -> datetime:
    return datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


async def test_failure_series_backs_off_until_done(world: World) -> None:
    rig = Rig(world)
    at = moment()
    act = Act("gorbushka", {"buy": False}, "gorbushka_fight")
    failed = ScenarioResult("failed", "timeout")
    spans = []
    for _ in range(3):
        await rig.loop._after(act, failed, at, at)
        spans.append(rig.loop._cooldowns["gorbushka"] - at)
    assert spans == [timedelta(minutes=m) for m in (5, 10, 20)]
    assert rig.notes.codes == ["scenario_failed"]
    await rig.loop._after(act, ScenarioResult("done", "gorbushka_fight"), at, at)
    await rig.loop._after(act, ScenarioResult("stopped", "unexpected_screen"), at, at)
    assert rig.loop._cooldowns["gorbushka"] - at == RETRY_AFTER
    assert rig.notes.codes == ["scenario_failed", "scenario_failed"]
    for _ in range(60):
        await rig.loop._after(act, failed, at, at)
    assert rig.loop._cooldowns["gorbushka"] - at == MAX_RETRY


async def test_refusal_cooldowns(world: World) -> None:
    rig = Rig(world)
    at = moment()
    job = Act("deed:job", {}, "best")
    await rig.loop._after(job, ScenarioResult("refused", "busy"), at, at)
    assert rig.loop._cooldowns == {"deed:job": at + timedelta(minutes=1)}
    await rig.loop._after(job, ScenarioResult("refused", "no_money"), at, at)
    assert rig.loop._cooldowns == {"deed:job": at + RETRY_AFTER}
    assert rig.notes.codes == []


async def test_battle_refusal_holds_all_deeds(world: World) -> None:
    world.game.on_text("/job", ("refusals", 3520502))
    await world.feed("profile", 3624478)
    await world.feed("gorbushka", 3516741)
    await world.settings.update(
        lambda s: s.model_copy(
            update={
                "features": s.features.model_copy(
                    update={"books": False, "cards_containers": False, "fastfood": False}
                )
            }
        ),
        changed_by="test",
    )
    rig = Rig(world)
    await rig.steps(2)
    assert world.game.payloads() == ["/job"]
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("deed:job", "refused", "battle_soon")
    ]
    assert set(DEEDS) <= set(rig.loop._cooldowns)
    assert rig.store.decisions[-1][1].kind == "wait"


async def test_failed_profile_refresh_does_not_hold_inventory(world: World) -> None:
    world.game.on_text("/inv", ("items", 3625102))
    rig = Rig(world)
    await rig.loop.step()
    assert [(r.scenario, r.status) for r in rig.store.runs] == [("refresh", "failed")]
    await world.feed("profile", 3624478)
    await rig.loop.step()
    assert world.game.payloads() == ["😎Я", "/inv"]
    assert set(rig.loop._cooldowns) == {"refresh:profile"}


async def test_cooldown_survives_run_journal_failure(world: World) -> None:
    rig = Rig(world)

    async def broken(run_id: int, status: str, reason: str, at: datetime) -> None:
        raise ConnectionError("db down")

    rig.store.run_finished = broken  # type: ignore[method-assign]
    with pytest.raises(ConnectionError):
        await rig.loop.step()
    assert set(rig.loop._cooldowns) == {"refresh:profile"}


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
        ("book", "stopped", "paused")
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


async def test_memory_store_closes_running_runs() -> None:
    store = MemoryPlannerStore()
    at = moment()
    await store.run_started(1, "book", {}, at)
    done = await store.run_started(1, "card", {}, at)
    await store.run_finished(done, "done", "card_used", at)
    assert await store.close_running(at + timedelta(minutes=1)) == 1
    assert [(r.status, r.reason) for r in store.runs] == [
        ("interrupted", "restart"),
        ("done", "card_used"),
    ]
