from datetime import date, datetime, timedelta
from typing import Any

from app.engine.planner.base import TIMER_MARGIN
from app.engine.planner.decide import TRIPS_MAX_AGE, decide, outlook
from app.engine.planner.types import Act, Decision, Wait, Wakeup
from app.engine.settings import FeaturesSection, Settings
from app.engine.state.model import (
    BusyState,
    CharacterState,
    GorbushkaState,
    Obs,
    Src,
    TripRef,
    TripsState,
    VehicleState,
)
from app.engine.state.reducer import TRIP_RESULT_GRACE, StateReducer
from tests.engine import trip_texts as t
from tests.engine.artifact_texts import game_text
from tests.engine.planner.test_artifact import mode, seen
from tests.engine.planner.test_decide import NOW, act, awake, config, m, r, verdicts, w
from tests.engine.planner.test_obligations import msk, run_in
from tests.engine.state.helpers import PARSER

TRIPS = config({"features": {"trips": True}})
CAR = VehicleState(name="🚕Ааавтомобиль", raw=8, money=20)
TRAM = VehicleState(name="🚃Трамвай", raw=10, money=30)
BIKE = VehicleState(name="🚲Велосипед", raw=5, money=0)


def ready(v: VehicleState, at: datetime = NOW) -> VehicleState:
    """Экран в `at` показал вид без строки «Через»: готов с момента экрана."""
    return v.model_copy(update={"ready_at": at})


def cooling(v: VehicleState, minutes: float) -> VehicleState:
    return v.model_copy(update={"ready_at": m(minutes)})


def transport(
    at: datetime = NOW, last: TripRef | None = None, src: Src = "screen", **vehicles: VehicleState
) -> Obs[TripsState]:
    return Obs(value=TripsState(vehicles=vehicles, last=last), at=at, src=src)


def with_trips(trips: Obs[TripsState] | None, at: datetime = NOW, **over: Any) -> CharacterState:
    fields: dict[str, Any] = {"raw": 100, **over}
    return awake(at, **fields).model_copy(update={"trips": trips})


def trip_names(decision: Decision) -> set[str]:
    names = {c.scenario for c in decision.candidates}
    if isinstance(decision, Act):
        names.add(decision.scenario)
    return names & {"trip", "trips_refresh"}


def by_vehicle(decision: Decision) -> dict[str, str]:
    """Вердикты кандидатов `trip` по видам."""
    return {
        str(c.params["vehicle"]): c.verdict
        for c in decision.candidates
        if c.scenario == "trip" and "vehicle" in c.params
    }


def test_rides_first_ready_vehicle_in_settings_order() -> None:
    s = with_trips(transport(car=cooling(CAR, 120), tram=ready(TRAM), bike=ready(BIKE)))
    decision = decide(s, TRIPS, NOW)
    assert act(decision) == ("trip", {"vehicle": "tram"})
    assert isinstance(decision, Act) and decision.reason == "tram ready"
    assert verdicts(decision) == {"trip": "chosen"}
    bike_first = config({"features": {"trips": True}, "trips": {"vehicles": ["bike", "tram"]}})
    assert act(decide(s, bike_first, NOW)) == ("trip", {"vehicle": "bike"})


def test_vehicle_removed_from_settings_is_never_ridden() -> None:
    cfg = config({"features": {"trips": True}, "trips": {"vehicles": ["car"]}})
    s = with_trips(transport(car=cooling(CAR, 120), tram=ready(TRAM)))
    decision = decide(s, cfg, NOW)
    assert act(decision)[0] == "deed:job"
    assert trip_names(decision) == set()
    view = outlook(s, cfg, NOW)
    assert Wakeup(r(120), "trip_ready", "car") in view.wakeups
    assert all(wk.key != "tram" for wk in view.wakeups)
    empty = config({"features": {"trips": True}, "trips": {"vehicles": []}})
    assert trip_names(decide(with_trips(None), empty, NOW)) == set()


def test_skips_missing_unavailable_and_expired_vehicles() -> None:
    gone = TRAM.model_copy(update={"expires_on": date(2026, 9, 25)})
    s = with_trips(transport(tram=ready(gone), bike=ready(BIKE)))
    assert act(decide(s, TRIPS, NOW)) == ("trip", {"vehicle": "bike"})
    # Последний день сезона — ещё можно.
    today = TRAM.model_copy(update={"expires_on": date(2026, 9, 26)})
    assert act(decide(with_trips(transport(tram=ready(today))), TRIPS, NOW)) == (
        "trip",
        {"vehicle": "tram"},
    )
    stub = VehicleState(name="🚃Трамвай", available=False)
    s = with_trips(transport(tram=stub, bike=ready(BIKE)))
    assert act(decide(s, TRIPS, NOW)) == ("trip", {"vehicle": "bike"})
    # Авто на экране нет — его не берём, хоть оно первое в списке.
    assert act(decide(with_trips(transport(bike=ready(BIKE))), TRIPS, NOW)) == (
        "trip",
        {"vehicle": "bike"},
    )


def test_waits_for_earliest_ready_vehicle() -> None:
    cfg = config({"features": {"trips": True, "deeds": False}})
    s = with_trips(transport(car=cooling(CAR, 120), tram=cooling(TRAM, 45)))
    decision = decide(s, cfg, NOW)
    assert isinstance(decision, Wait)
    assert (decision.until, decision.reason) == (r(45), "trip_ready:tram")
    wakeups = outlook(s, cfg, NOW).wakeups
    assert {Wakeup(r(45), "trip_ready", "tram"), Wakeup(r(120), "trip_ready", "car")} <= set(
        wakeups
    )


def test_feature_off_never_plans_trip_nor_refresh() -> None:
    off = config({"features": {"trips": False}})
    states = (
        with_trips(None),
        with_trips(transport(src="doubtful")),
        with_trips(transport(at=NOW - TRIPS_MAX_AGE - timedelta(minutes=1), tram=ready(TRAM))),
        with_trips(transport(at=m(-60), tram=cooling(TRAM, -5))),
        with_trips(transport(tram=ready(TRAM))),
        with_trips(transport(tram=cooling(TRAM, 30))),
    )
    for s in states:
        decision = decide(s, off, NOW)
        assert trip_names(decision) == set()
        view = outlook(s, off, NOW)
        assert trip_names(view.decision) == set()
        assert all(trip_names(a) == set() for a in view.also_ready)
        assert all(wk.kind != "trip_ready" and wk.key != "trips" for wk in view.wakeups)


def test_busy_or_sleeping_character_does_not_ride() -> None:
    working = with_trips(transport(tram=ready(TRAM)), busy=BusyState(activity="job", until=m(5)))
    decision = decide(working, TRIPS, NOW)
    assert isinstance(decision, Wait) and verdicts(decision)["trip"] == "busy"
    asleep = with_trips(
        transport(tram=ready(TRAM)), busy=BusyState(activity="sleep_hotel", until=m(300))
    )
    assert trip_names(decide(asleep, TRIPS, NOW)) == set()
    # Занят без данных о транспорте — экран тоже ждёт.
    unknown = with_trips(None, busy=BusyState(activity="job", until=m(5)))
    decision = decide(unknown, TRIPS, NOW)
    assert isinstance(decision, Wait) and verdicts(decision)["trip"] == "busy"


def test_no_trip_inside_metro() -> None:
    # Последний экран забега старше 2 часов — не продолжается, но персонаж ещё в метро.
    now = msk(13)
    s = with_trips(
        transport(at=now, tram=ready(TRAM, now)),
        at=now,
        metro_message=run_in(now - timedelta(hours=3)),
    )
    decision = decide(s, config({"features": {"trips": True, "metro": True}}), now)
    assert verdicts(decision)["trip"] == "in_metro"
    assert not (isinstance(decision, Act) and decision.scenario == "trip")


def test_no_trip_while_metro_exit_unconfirmed() -> None:
    now = msk(13)
    pending = run_in(now - timedelta(minutes=10))
    pending = pending.model_copy(
        update={"value": pending.value.model_copy(update={"exit_at": now - timedelta(minutes=10)})}
    )
    s = with_trips(transport(at=now, tram=ready(TRAM, now)), at=now, metro_message=pending)
    decision = decide(s, config({"features": {"trips": True, "metro": True}}), now)
    assert verdicts(decision)["trip"] == "in_metro"
    assert not isinstance(decision, Act)


def test_needs_raw_for_price() -> None:
    decision = decide(with_trips(transport(tram=ready(TRAM)), raw=9), TRIPS, NOW)
    assert act(decision)[0] == "deed:job"
    assert verdicts(decision)["trip"] == "no_raw"
    assert act(decide(with_trips(transport(tram=ready(TRAM)), raw=10), TRIPS, NOW))[0] == "trip"


def test_unaffordable_vehicle_yields_to_next_ready_one() -> None:
    # Порядок — приоритет среди тех, на которых можно ехать сейчас.
    both = transport(car=ready(CAR), bike=ready(BIKE))
    # Вечером держится отель (210💵): на 🚕 (20💵) не хватает, 🚲 денег не стоит.
    hotel = decide(with_trips(both, money=225, sleep_deadline=m(4 * 60)), TRIPS, NOW)
    assert act(hotel) == ("trip", {"vehicle": "bike"})
    assert by_vehicle(hotel) == {"car": "no_money", "bike": "chosen"}
    # 🔩 6: на 🚕 (8🔩) не хватает, на 🚲 (5🔩) — да.
    raw = decide(with_trips(both, raw=6), TRIPS, NOW)
    assert act(raw) == ("trip", {"vehicle": "bike"})
    assert by_vehicle(raw) == {"car": "no_raw", "bike": "chosen"}
    # Хватает на оба — первый по порядку.
    assert act(decide(with_trips(both), TRIPS, NOW)) == ("trip", {"vehicle": "car"})
    # Не хватает ни на один — ждём, отказы у обоих.
    none = decide(with_trips(both, raw=4), TRIPS, NOW)
    assert act(none)[0] == "deed:job"
    assert by_vehicle(none) == {"car": "no_raw", "bike": "no_raw"}


def test_money_only_above_ticket_and_hotel_reserves() -> None:
    g = GorbushkaState(state="need_ticket")
    # Билет Горбушки не по карману (📚 мало) — но 120💵 на него держатся.
    short = with_trips(transport(tram=ready(TRAM)), money=140, knowledge=0, gorbushka=g)
    decision = decide(short, TRIPS, NOW)
    assert verdicts(decision)["trip"] == "no_money"
    enough = with_trips(transport(tram=ready(TRAM)), money=150, knowledge=0, gorbushka=g)
    assert act(decide(enough, TRIPS, NOW)) == ("trip", {"vehicle": "tram"})
    # За 3 часа до сна в отеле держится его цена (210 = 3💵 × уровень 70).
    hotel = with_trips(transport(tram=ready(TRAM)), money=230, sleep_deadline=m(4 * 60))
    assert verdicts(decide(hotel, TRIPS, NOW))["trip"] == "no_money"
    far = with_trips(transport(tram=ready(TRAM)), money=230)
    assert act(decide(far, TRIPS, NOW)) == ("trip", {"vehicle": "tram"})
    # Без 💵 в цене резервы не мешают.
    poor = with_trips(transport(bike=ready(BIKE)), money=0)
    assert act(decide(poor, TRIPS, NOW)) == ("trip", {"vehicle": "bike"})


def test_negative_money_remainder_blocks_only_paid_vehicles() -> None:
    # Резерв билета (120💵) больше всех денег: остаток отрицателен.
    g = GorbushkaState(state="need_ticket")
    both = transport(car=ready(CAR), bike=ready(BIKE))
    decision = decide(with_trips(both, money=50, knowledge=0, gorbushka=g), TRIPS, NOW)
    assert act(decision) == ("trip", {"vehicle": "bike"})
    assert by_vehicle(decision) == {"car": "no_money", "bike": "chosen"}
    car = decide(
        with_trips(transport(car=ready(CAR)), money=50, knowledge=0, gorbushka=g), TRIPS, NOW
    )
    assert verdicts(car)["trip"] == "no_money"


def test_trip_fits_battle_sleep_and_factory_windows_like_deeds() -> None:
    def at(now: datetime, **over: Any) -> CharacterState:
        return with_trips(transport(at=now, tram=ready(TRAM, now)), at=now, **over)

    # Битва в 11:00 (UTC): поездка до 10:55 задела бы окно за 6 минут до неё.
    battle = m(60)
    late = decide(at(m(50), battle_at=battle), TRIPS, m(50))
    assert verdicts(late)["trip"] == "battle_window"
    assert act(decide(at(m(44), battle_at=battle), TRIPS, m(44)))[0] == "trip"
    no_sleep = config({"features": {"trips": True, "sleep": False}})
    tight = decide(at(NOW, sleep_deadline=m(8)), no_sleep, NOW)
    assert verdicts(tight)["trip"] == "sleep_deadline"
    assert act(decide(at(NOW, sleep_deadline=m(12)), no_sleep, NOW))[0] == "trip"
    factory = config({"features": {"trips": True, "factory": True}})
    early = msk(17, 55)
    assert verdicts(decide(at(early), factory, early))["trip"] == "factory_window"
    before = msk(17, 49)
    assert act(decide(at(before), factory, before))[0] == "trip"


def test_motivation_at_cap_goes_to_deed_first() -> None:
    def capped(**over: Any) -> CharacterState:
        return with_trips(transport(tram=ready(TRAM)), motivation_max=40, **over)

    full = decide(capped(motivation=40, motivation_next_at=None), TRIPS, NOW)
    assert act(full)[0] == "deed:job"
    assert verdicts(full)["trip"] == "motivation_cap"
    # Тик регенерации за время поездки довёл бы до максимума — тоже сначала дело.
    near = decide(capped(motivation=39, motivation_next_at=m(5)), TRIPS, NOW)
    assert verdicts(near)["trip"] == "motivation_cap"
    far = decide(capped(motivation=39, motivation_next_at=m(30)), TRIPS, NOW)
    assert act(far) == ("trip", {"vehicle": "tram"})
    # У максимума, но выполнимого дела нет — поездка.
    no_deeds = config({"features": {"trips": True, "deeds": False}})
    alone = decide(capped(motivation=40, motivation_next_at=None), no_deeds, NOW)
    assert act(alone) == ("trip", {"vehicle": "tram"})
    # Дело есть, но не по карману — поездка не ждёт.
    broke = with_trips(
        transport(bike=ready(BIKE)),
        motivation=40,
        motivation_max=40,
        motivation_next_at=None,
        money=0,
    )
    dconv = config({"features": {"trips": True}, "strategy": {"deeds": ["dconv"]}})
    decision = decide(broke, dconv, NOW)
    assert act(decision) == ("trip", {"vehicle": "bike"})
    assert verdicts(decision) == {"trip": "chosen"}


def test_trips_allowed_during_artifact_collection() -> None:
    s = with_trips(transport(tram=ready(TRAM)), artifact_collect=seen())
    decision = decide(s, mode(TRIPS), NOW)
    assert act(decision) == ("trip", {"vehicle": "tram"})


def test_waits_for_previous_trip_result() -> None:
    started = m(-10.5)
    pending = TripRef(vehicle="bike", started_at=started)
    s = with_trips(transport(at=started, last=pending, tram=ready(TRAM, m(-30))))
    decision = decide(s, TRIPS, NOW)
    assert verdicts(decision)["trip"] == "trip_pending"
    assert act(decision)[0] == "deed:job"
    wake = started + timedelta(minutes=10) + TRIP_RESULT_GRACE
    assert Wakeup(wake + TIMER_MARGIN, "trip_result") in outlook(s, TRIPS, NOW).wakeups
    # Редьюсер примет итог прошлой поездки до старт + 10 мин + запас включительно: до этого
    # момента новая поездка забрала бы его себе.
    edge = NOW - timedelta(minutes=10) - TRIP_RESULT_GRACE
    s = with_trips(
        transport(at=edge, last=TripRef(vehicle="bike", started_at=edge), tram=ready(TRAM, m(-30)))
    )
    assert verdicts(decide(s, TRIPS, NOW))["trip"] == "trip_pending"
    # Итог не пришёл и после окна редьюсера — не ждём дальше.
    old = edge - timedelta(seconds=1)
    s = with_trips(
        transport(at=old, last=TripRef(vehicle="bike", started_at=old), tram=ready(TRAM, m(-30)))
    )
    assert act(decide(s, TRIPS, NOW)) == ("trip", {"vehicle": "tram"})
    done = pending.model_copy(update={"done": True, "result_id": 1})
    s = with_trips(transport(at=started, last=done, tram=ready(TRAM, m(-30))))
    assert act(decide(s, TRIPS, NOW)) == ("trip", {"vehicle": "tram"})


def test_refreshes_unknown_doubtful_or_old_transport_screen() -> None:
    for trips in (
        None,
        transport(src="doubtful", tram=ready(TRAM)),
        transport(at=NOW - TRIPS_MAX_AGE - timedelta(seconds=1), tram=ready(TRAM)),
    ):
        decision = decide(with_trips(trips), TRIPS, NOW)
        assert act(decision) == ("trips_refresh", {})
        assert verdicts(decision) == {"trip": "stale:trips", "trips_refresh": "chosen"}
    young = transport(at=NOW - TRIPS_MAX_AGE + timedelta(minutes=1), tram=cooling(TRAM, 120))
    view = outlook(with_trips(young), TRIPS, NOW)
    assert trip_names(view.decision) == set()
    assert Wakeup(w(1), "refresh", "trips") in view.wakeups


def test_first_message_is_refusal_or_start_then_screen_is_requested() -> None:
    # Отказ и старт до первого экрана видят один вид: остальных планировщик не знает.
    for text in (t.REFUSAL_SLED, t.START_CAR):
        msg = game_text(text, at=NOW, msg_id=1)
        saved = StateReducer().apply({}, msg, PARSER.parse(msg))
        obs = Obs[TripsState].model_validate(saved["trips"])
        decision = decide(with_trips(obs), TRIPS, NOW)
        assert act(decision) == ("trips_refresh", {})
        assert verdicts(decision)["trip"] == "stale:trips"


def test_ready_time_passed_without_screen_rides_directly() -> None:
    # Готовность посчитана (старт + кулдаун), экран после неё не видели: сценарий поездки сам
    # откроет экран и на кулдауне кнопку не нажмёт — отдельное обновление не нужно.
    s = with_trips(transport(at=m(-60), tram=cooling(TRAM, -5)))
    decision = decide(s, TRIPS, NOW)
    assert act(decision) == ("trip", {"vehicle": "tram"})
    assert isinstance(decision, Act) and decision.reason == "tram ready"
    # Минута на округление кулдауна с экрана ещё не прошла — ждём.
    s = with_trips(transport(at=m(-60), tram=cooling(TRAM, -0.5)))
    view = outlook(s, config({"features": {"trips": True, "deeds": False}}), NOW)
    assert isinstance(view.decision, Wait)
    assert view.decision.reason == "trip_ready:tram"
    # Цена вида неизвестна (вид знаком только по отказу) — экран.
    unknown = VehicleState(name="🚃Трамвай", ready_at=m(-60))
    decision = decide(with_trips(transport(tram=unknown, bike=ready(BIKE))), TRIPS, NOW)
    assert act(decision) == ("trips_refresh", {})
    assert isinstance(decision, Act) and decision.reason == "tram price unknown"


def test_refresh_is_rate_limited() -> None:
    s = with_trips(None)
    decision = decide(s, TRIPS, NOW, last_refresh={"trips": m(-1)})
    assert act(decision)[0] == "deed:job"
    assert verdicts(decision)["trips_refresh"] == "rate_limited"
    view = outlook(s, TRIPS, NOW, last_refresh={"trips": m(-1)})
    assert Wakeup(w(1), "refresh", "trips") in view.wakeups
    assert act(decide(s, TRIPS, NOW, last_refresh={"trips": m(-2)})) == ("trips_refresh", {})


def test_outlook_shows_deed_behind_trip() -> None:
    s = with_trips(transport(car=cooling(CAR, 120), tram=ready(TRAM)))
    view = outlook(s, TRIPS, NOW)
    assert isinstance(view.decision, Act) and view.decision.scenario == "trip"
    assert [a.scenario for a in view.also_ready] == ["deed:job"]
    assert Wakeup(r(120), "trip_ready", "car") in view.wakeups


def test_default_flags_with_all_vehicles_cooling_change_only_timers() -> None:
    # Флаги по умолчанию (поездки включены): свежий экран, все виды на кулдауне — решение то же,
    # что без поездок; добавляются только таймеры.
    on = Settings()
    off = Settings(features=FeaturesSection(trips=False))
    assert on.features.trips is True
    cooling_all = transport(
        car=cooling(CAR, 600), tram=cooling(TRAM, 700), bike=cooling(BIKE, 800)
    )
    for s in (with_trips(cooling_all), with_trips(cooling_all, motivation=0)):
        with_on, with_off = decide(s, on, NOW), decide(s, off, NOW)
        assert type(with_on) is type(with_off)
        assert with_on.candidates == with_off.candidates
        if isinstance(with_on, Act):
            assert with_on == with_off
        view_on, view_off = outlook(s, on, NOW), outlook(s, off, NOW)
        extra = set(view_on.wakeups) - set(view_off.wakeups)
        assert {(x.kind, x.key) for x in extra} == {
            ("trip_ready", "car"),
            ("trip_ready", "tram"),
            ("trip_ready", "bike"),
            ("refresh", "trips"),
        }
        assert set(view_off.wakeups) <= set(view_on.wakeups)
