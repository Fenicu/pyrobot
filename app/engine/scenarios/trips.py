"""Поездки: `🏢Офис` → `🚦Поездки` → экран «Транспорт» → кнопка вида → старт поездки."""

from __future__ import annotations

from app.engine.bus import Delivery
from app.engine.events import Event
from app.engine.gateway.types import Match, Verdict
from app.engine.parsing.screens import InfoScreen
from app.engine.parsing.trips import VEHICLES, TripRefused, TripsScreen, TripStarted
from app.engine.scenarios.context import Predicate, ScenarioContext, Step, expect_events
from app.engine.scenarios.library import Params, ScenarioResult, wrong_screen
from app.engine.state.model import CharacterState

OFFICE = "🏢Офис"
TRIPS = "🚦Поездки"


def _is_office(event: Event) -> bool:
    return isinstance(event, InfoScreen) and event.name == "office"


async def _open(ctx: ScenarioContext) -> TripsScreen | ScenarioResult:
    """Экран «Транспорт»: `🚦Поездки` — кнопка меню офиса, поэтому только следом за ним, без
    безопасной точки между шагами (срочное действие сбило бы меню)."""
    office = await ctx.send(OFFICE, expect_events(InfoScreen, accept=_is_office))
    if office.step is not Step.OK:
        return wrong_screen(office)
    opened = await ctx.send(TRIPS, expect_events(TripsScreen))
    screen = opened.first(TripsScreen)
    if opened.step is not Step.OK or screen is None:
        return wrong_screen(opened)
    return screen


def _started(key: str) -> Predicate:
    """Старт нашего вида (без значка в тексте — вид `None`) или отказ по кулдауну нашего вида;
    «занят» и общая справка — отказы `expect_events`."""
    base = expect_events(
        TripStarted,
        accept=lambda e: isinstance(e, TripStarted) and e.vehicle in (key, None),
    )

    def predicate(delivery: Delivery) -> Match | None:
        for event in delivery.events:
            if isinstance(event, TripRefused) and event.vehicle == key:
                return Match(Verdict.REFUSED, "cooldown")
        return base(delivery)

    return predicate


async def trips_refresh(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    async with ctx.lease("trips"):
        opened = await _open(ctx)
    return opened if isinstance(opened, ScenarioResult) else ScenarioResult("done", "screen")


async def trip(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    """Поездка на виде `vehicle` (ключ из `VEHICLES`). Вид, которого нет на экране или который
    недоступен, — `nothing not_available`; на кулдауне по экрану — `nothing cooldown` без
    кнопки. Кнопка вида работает из меню «Транспорт»: сразу после экрана, под арендой. Отказ игры
    по кулдауну — `refused cooldown`, занятость — `refused busy`, общая справка —
    `failed wrong_screen`.
    """
    vehicle = VEHICLES[str(params["vehicle"])]
    async with ctx.lease("trips"):
        opened = await _open(ctx)
        if isinstance(opened, ScenarioResult):
            return opened
        entry = next((v for v in opened.vehicles if v.key == vehicle.key), None)
        if entry is None or not entry.available:
            return ScenarioResult("nothing", "not_available")
        if (entry.left_s or 0) > 0:
            return ScenarioResult("nothing", "cooldown")
        started = await ctx.send(vehicle.button, _started(vehicle.key))
    if started.step is Step.OK:
        return ScenarioResult("done", "started")
    return wrong_screen(started)
