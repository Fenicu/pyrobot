from datetime import timedelta

import pytest

from app.engine.parsing.trips import VEHICLES
from app.engine.scenarios.library import run_scenario
from app.engine.scenarios.trips import trip, trips_refresh
from app.engine.state.model import CharacterState
from tests.engine.artifact_texts import game_text
from tests.engine.fakegame import LIVE, Ref, World, running_world
from tests.engine.scenarios.certify import certifies
from tests.engine.scenarios.conftest import context
from tests.engine.trip_texts import (
    BUSY_DURING_TRIP,
    REFUSAL_BIKE,
    REFUSAL_CAR,
    SCREEN,
    SCREEN_ALL_COOLDOWN,
    SCREEN_SCOOTER_COOLDOWN,
    SCREEN_SLED_APRIL,
    SCREEN_TRACTOR,
    SCREEN_TRAM_RAIL,
    START_BIKE,
    START_CAR,
    START_SLED,
    START_TRAM,
)

OFFICE = ("screens", 3623175)
WRONG_SCREEN = ("refusals", 3625754)
BUSY = ("refusals", 3604198)
SCREEN_SCOOTER_READY = SCREEN_SCOOTER_COOLDOWN.replace("\nЧерез 2ч. 37 мин.", "")
# Тексты старта самоката и трактора неизвестны: общий якорь «Перед поездкой» без значка вида.
START_UNKNOWN_VEHICLE = (
    "Выехал покататься. Перед поездкой потратил 10🔩. Вернёшься через 10 минут."
)
FEATURE_OFF = LIVE.model_copy(
    update={"features": LIVE.features.model_copy(update={"trips": False})}
)


def open_transport(world: World, screen: Ref | str) -> None:
    world.game.on_text("🏢Офис", OFFICE)
    world.game.on_text("🚦Поездки", game_text(screen) if isinstance(screen, str) else screen)


async def run(world: World, vehicle: str) -> tuple[str, str]:
    ctx = context(world, scenario="trip")
    result = await run_scenario(trip, ctx, CharacterState(), {"vehicle": vehicle})
    return result.status, result.reason


@certifies("trip")
@pytest.mark.parametrize(
    ("vehicle", "screen", "start"),
    [
        ("bike", SCREEN, START_BIKE),
        ("car", SCREEN, START_CAR),
        ("tram", SCREEN, START_TRAM),
        ("sled", SCREEN_SLED_APRIL, START_SLED),
        ("scooter", SCREEN_SCOOTER_READY, START_UNKNOWN_VEHICLE),
        ("tractor", SCREEN_TRACTOR, START_UNKNOWN_VEHICLE),
    ],
)
async def test_start_sends_vehicle_button_right_after_screen(
    world: World, vehicle: str, screen: str, start: str
) -> None:
    open_transport(world, screen)
    world.game.on_text(VEHICLES[vehicle].button, game_text(start))
    assert await run(world, vehicle) == ("done", "started")
    assert world.game.payloads() == ["🏢Офис", "🚦Поездки", VEHICLES[vehicle].button]
    assert world.gateway.lease is None
    trips = world.state.trips
    assert trips is not None and trips.value.last is not None
    assert trips.value.vehicles[vehicle].ready_at is not None


@certifies("trip")
async def test_start_marks_character_busy_with_trip(world: World) -> None:
    open_transport(world, SCREEN)
    world.game.on_text("🚲Велик", game_text(START_BIKE))
    assert await run(world, "bike") == ("done", "started")
    busy = world.state.busy
    assert busy is not None and busy.value.activity == "trip"
    assert world.state.trips is not None and world.state.trips.value.last is not None
    assert world.state.trips.value.last.vehicle == "bike"


@certifies("trip")
async def test_cooldown_on_screen_sends_no_button(world: World) -> None:
    open_transport(world, SCREEN_ALL_COOLDOWN)
    assert await run(world, "bike") == ("nothing", "cooldown")
    assert world.game.payloads() == ["🏢Офис", "🚦Поездки"]
    assert world.gateway.lease is None
    trips = world.state.trips
    assert trips is not None
    ready = trips.value.vehicles["bike"].ready_at
    assert ready is not None and ready > trips.at + timedelta(hours=22)


@certifies("trip")
@pytest.mark.parametrize(
    ("vehicle", "screen"),
    [
        ("tram", SCREEN_TRAM_RAIL),
        ("tractor", SCREEN),
        ("sled", SCREEN),
    ],
)
async def test_vehicle_missing_or_stub_is_not_available(
    world: World, vehicle: str, screen: str
) -> None:
    open_transport(world, screen)
    assert await run(world, vehicle) == ("nothing", "not_available")
    assert world.game.payloads() == ["🏢Офис", "🚦Поездки"]


@certifies("trip")
async def test_cooldown_refusal_after_ready_screen(world: World) -> None:
    open_transport(world, SCREEN)
    world.game.on_text("🚲Велик", game_text(REFUSAL_BIKE))
    assert await run(world, "bike") == ("refused", "cooldown")
    assert world.game.payloads() == ["🏢Офис", "🚦Поездки", "🚲Велик"]
    trips = world.state.trips
    assert trips is not None
    ready = trips.value.vehicles["bike"].ready_at
    assert ready is not None and ready > trips.at + timedelta(hours=22)


@certifies("trip")
async def test_refusal_of_other_vehicle_is_not_ours(world: World) -> None:
    open_transport(world, SCREEN)
    world.game.on_text("🚲Велик", game_text(REFUSAL_CAR))
    assert await run(world, "bike") == ("failed", "timeout")


@certifies("trip")
async def test_busy_is_refused(world: World) -> None:
    open_transport(world, SCREEN)
    world.game.on_text("🚕Тачка", game_text(BUSY_DURING_TRIP))
    assert await run(world, "car") == ("refused", "busy")
    assert world.game.payloads() == ["🏢Офис", "🚦Поездки", "🚕Тачка"]


@certifies("trip")
async def test_busy_real_text_is_refused(world: World) -> None:
    open_transport(world, SCREEN)
    world.game.on_text("🚕Тачка", BUSY)
    assert await run(world, "car") == ("refused", "busy")


@certifies("trip")
async def test_stub_instead_of_screen_is_wrong_screen(world: World) -> None:
    open_transport(world, WRONG_SCREEN)
    assert await run(world, "bike") == ("failed", "wrong_screen")
    assert world.game.payloads() == ["🏢Офис", "🚦Поездки"]
    assert world.gateway.lease is None


@certifies("trip")
async def test_stub_instead_of_start_is_wrong_screen(world: World) -> None:
    open_transport(world, SCREEN)
    world.game.on_text("🚲Велик", WRONG_SCREEN)
    assert await run(world, "bike") == ("failed", "wrong_screen")
    assert world.gateway.lease is None


@certifies("trip")
async def test_unclear_start_is_failed(world: World) -> None:
    open_transport(world, SCREEN)
    assert await run(world, "bike") == ("failed", "timeout")
    assert world.game.payloads() == ["🏢Офис", "🚦Поездки", "🚲Велик"]


@certifies("trip")
async def test_feature_off_stops_before_button() -> None:
    async for world in running_world(FEATURE_OFF):
        open_transport(world, SCREEN)
        world.game.on_text("🚲Велик", game_text(START_BIKE))
        assert await run(world, "bike") == ("failed", "feature_off:trips")
        assert world.game.payloads() == ["🏢Офис", "🚦Поездки"]


@certifies("trips_refresh")
async def test_refresh_reads_screen_via_office(world: World) -> None:
    open_transport(world, SCREEN_ALL_COOLDOWN)
    ctx = context(world, scenario="trips_refresh")
    result = await run_scenario(trips_refresh, ctx, CharacterState(), {})
    assert (result.status, result.reason) == ("done", "screen")
    assert world.game.payloads() == ["🏢Офис", "🚦Поездки"]
    assert world.gateway.lease is None
    trips = world.state.trips
    assert trips is not None and set(trips.value.vehicles) == {"bike", "car", "tram"}


@certifies("trips_refresh")
async def test_refresh_without_vehicle_buttons(world: World) -> None:
    open_transport(world, SCREEN)
    ctx = context(world, scenario="trips_refresh")
    await run_scenario(trips_refresh, ctx, CharacterState(), {})
    assert all(p in ("🏢Офис", "🚦Поездки") for p in world.game.payloads())


@certifies("trips_refresh")
async def test_refresh_off_screen_is_wrong_screen(world: World) -> None:
    open_transport(world, WRONG_SCREEN)
    ctx = context(world, scenario="trips_refresh")
    result = await run_scenario(trips_refresh, ctx, CharacterState(), {})
    assert (result.status, result.reason) == ("failed", "wrong_screen")
    assert world.gateway.lease is None


@certifies("trips_refresh")
async def test_refresh_without_answer_fails(world: World) -> None:
    world.game.on_text("🏢Офис", OFFICE)
    ctx = context(world, scenario="trips_refresh")
    result = await run_scenario(trips_refresh, ctx, CharacterState(), {})
    assert (result.status, result.reason) == ("failed", "timeout")
    assert world.game.payloads() == ["🏢Офис", "🚦Поездки"]
