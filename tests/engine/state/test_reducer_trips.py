from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from app.engine.parsing.trips import TRIP_SPAN, VEHICLES
from app.engine.state.ledger import Effect
from app.engine.state.model import BusyState, TripRef, TripsState, VehicleState
from app.engine.state.reducer import StateReducer
from tests.engine import trip_texts as t
from tests.engine.artifact_texts import game_text
from tests.engine.state.helpers import PARSER, at, feed, value
from tests.fixtures import game_msg

H = timedelta(hours=1)


def apply(
    reducer: StateReducer, state: dict[str, Any], text: str, minutes: float, msg_id: int
) -> tuple[dict[str, Any], tuple[Effect, ...]]:
    msg = game_text(text, at=at(minutes), msg_id=msg_id)
    new, effects = reducer.reduce(state, msg, PARSER.parse(msg))
    return new, tuple(effects)


def trips(state: dict[str, Any]) -> TripsState:
    return TripsState.model_validate(value(state, "trips"))


def busy(state: dict[str, Any]) -> BusyState | None:
    seen = value(state, "busy")
    return None if seen is None else BusyState.model_validate(seen)


def profiled(reducer: StateReducer) -> dict[str, Any]:
    return feed(reducer, {}, "profile", 3624478, 0)


def test_screen_prices_ready_and_placeholders() -> None:
    s, effects = apply(StateReducer(), {}, t.SCREEN_TRAM_RAIL, 1, 1)
    assert effects == ()
    assert s["trips"]["src"] == "screen"
    assert trips(s) == TripsState(
        vehicles={
            "scooter": VehicleState(
                name="🛴Самокат", raw=10, money=30, ready_at=at(1) + 2 * H + timedelta(minutes=26)
            ),
            "bike": VehicleState(
                name="🚲Велосипед", raw=5, money=0, ready_at=at(1) + 20 * H + timedelta(minutes=36)
            ),
            "car": VehicleState(
                name="🚕Ааавтомобиль",
                raw=8,
                money=20,
                ready_at=at(1) + 17 * H + timedelta(minutes=49),
            ),
            # Заглушка без цены: вид недоступен, когда можно ехать — неизвестно.
            "tram": VehicleState(name="🚃Трамвай", available=False),
        }
    )
    s, _ = apply(StateReducer(), {}, t.SCREEN, 1, 1)
    # Строки «Через» нет — вид готов уже на момент экрана.
    assert {k: v.ready_at for k, v in trips(s).vehicles.items()} == dict.fromkeys(
        ("bike", "car", "tram"), at(1)
    )


def test_screen_season_end_is_a_date() -> None:
    msg = replace(game_text(t.SCREEN_TRACTOR_COOLDOWN, msg_id=1), date=at(0), created_at=at(0))
    s = StateReducer().apply({}, msg, PARSER.parse(msg))
    # Экран 26.09.2026: «годен до 31 января» — ближайшее 31 января.
    assert trips(s).vehicles["tractor"].expires_on == date(2027, 1, 31)
    assert trips(s).vehicles["car"].expires_on is None


def test_start_busy_costs_cooldown_and_ledger() -> None:
    r = StateReducer()
    s = profiled(r)
    s, _ = apply(r, s, t.SCREEN, 1, 2)
    money, raw = value(s, "money"), value(s, "raw")
    s, effects = apply(r, s, t.START_CAR, 2, 3)
    assert busy(s) == BusyState(activity="trip", until=at(2) + TRIP_SPAN)
    assert (value(s, "money"), value(s, "raw")) == (money - 20, raw - 8)
    assert effects == (Effect("trip_start", {"money": -20, "raw": -8}),)
    state = trips(s)
    assert state.vehicles["car"].ready_at == at(2) + VEHICLES["car"].cooldown
    assert state.vehicles["bike"].ready_at == at(1)
    assert state.last == TripRef(vehicle="car", started_at=at(2))
    assert s["trips"]["src"] == "derived"


def test_start_without_screen_knows_the_vehicle_from_the_text() -> None:
    s, _ = apply(StateReducer(), {}, t.START_SLED, 0, 1)
    assert trips(s).vehicles == {
        "sled": VehicleState(name="🛷Санки", raw=9, money=25, ready_at=at(0) + 18 * H)
    }


def test_start_of_unknown_vehicle_only_busy_and_costs() -> None:
    text = "Завёл 🚁Вертолёт. Перед поездкой залил 50🔩 и 100 💵. Вернёшься через 10 минут."
    s, effects = apply(StateReducer(), {}, text, 0, 1)
    assert busy(s) == BusyState(activity="trip", until=at(0) + TRIP_SPAN)
    assert trips(s) == TripsState(last=TripRef(vehicle=None, started_at=at(0)))
    assert effects == (Effect("trip_start", {"money": -100, "raw": -50}),)


def test_refusal_sets_ready_at_of_the_vehicle() -> None:
    r = StateReducer()
    s, _ = apply(r, {}, t.SCREEN, 0, 1)
    s, effects = apply(r, s, t.REFUSAL_TRAM, 5, 2)
    assert effects == ()
    assert trips(s).vehicles["tram"].ready_at == at(5) + 17 * H + timedelta(minutes=8)
    assert trips(s).vehicles["tram"].raw == 10
    # Вид, которого ещё не видели, — с ценой «неизвестно».
    s, _ = apply(StateReducer(), {}, t.REFUSAL_SLED, 0, 1)
    assert trips(s).vehicles == {
        "sled": VehicleState(name="🛷Санки", ready_at=at(0) + 6 * H + timedelta(minutes=21))
    }


def test_live_bike_trip_result_goes_to_ledger_as_trip() -> None:
    r = StateReducer()
    s = profiled(r)
    knowledge = value(s, "knowledge")
    s, _ = apply(r, s, t.SCREEN, 1, 2)
    s, _ = apply(r, s, t.START_BIKE, 1.1, 3)
    s, _ = apply(r, s, t.BUSY_DURING_TRIP, 1.2, 4)
    # Отказ «занят» держит вид занятости.
    assert busy(s) == BusyState(activity="trip", until=at(1.2) + timedelta(minutes=9, seconds=55))
    s, effects = apply(r, s, t.RESULT_BIKE, 11.1, 5)
    assert effects == (Effect("trip", {"knowledge": 16}),)
    assert value(s, "knowledge") == knowledge + 16
    assert busy(s) is None
    last = trips(s).last
    assert last is not None and last.done
    # Второе такое же сообщение — уже не итог этой поездки.
    s2, effects = apply(r, s, t.RESULT_TRAM_EXP, 11.5, 6)
    assert effects == () and value(s2, "exp") == value(s, "exp")


def test_rewards_only_without_trip_is_not_applied() -> None:
    r = StateReducer()
    s = profiled(r)
    s2, effects = apply(r, s, t.RESULT_TRAM_EXP, 5, 2)
    assert effects == () and value(s2, "exp") == value(s, "exp")
    # Поездка давно кончилась: итог не её.
    s, _ = apply(r, s, t.START_TRAM, 10, 3)
    late = 10 + TRIP_SPAN.total_seconds() / 60 + 30
    s2, effects = apply(r, s, t.RESULT_TRAM_EXP, late, 4)
    assert effects == () and value(s2, "exp") == value(s, "exp")


def test_result_while_busy_with_a_deed_keeps_the_deed() -> None:
    r = StateReducer()
    s = profiled(r)
    s, _ = apply(r, s, t.START_TRAM, 0, 2)
    s = feed(r, s, "activities", 3517276, 9)
    deed = busy(s)
    assert deed is not None and deed.activity != "trip"
    s, effects = apply(r, s, t.RESULT_TRAM_MONEY, 10, 3)
    assert effects == (Effect("trip", {"money": 215}),)
    assert busy(s) == deed


def test_screen_keeps_the_running_trip() -> None:
    r = StateReducer()
    s, _ = apply(r, {}, t.START_BIKE, 0, 1)
    s, _ = apply(r, s, t.SCREEN_ALL_COOLDOWN, 1, 2)
    assert trips(s).last == TripRef(vehicle="bike", started_at=at(0))
    assert trips(s).vehicles["bike"].ready_at == at(1) + 22 * H + timedelta(minutes=49)


def test_profile_without_trip_line_keeps_the_trip_busy() -> None:
    # Поездку на 🚲/🚕 профиль не показывает.
    r = StateReducer()
    s, _ = apply(r, {}, t.START_BIKE, 0, 1)
    during = feed(r, s, "profile", 3624478, 5)
    assert busy(during) == BusyState(activity="trip", until=at(0) + TRIP_SPAN)
    after = feed(r, s, "profile", 3624478, 11)
    assert busy(after) is None


def test_profile_trip_line_is_trip_busy() -> None:
    msg = game_msg("profile", 3623869)
    assert msg.text is not None
    text = msg.text.replace("💻Работаешь (41 сек.)", t.PROFILE_TRAM_LINE)
    profile = replace(msg, text=text, date=at(0), created_at=at(0))
    s = StateReducer().apply({}, profile, PARSER.parse(profile))
    assert busy(s) == BusyState(activity="trip", until=at(0) + timedelta(minutes=9, seconds=58))
