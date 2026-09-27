"""Шаг `lottery`: окно продажи 19:17–21:05 MSK, цели по валютам, запасы и резервы, смена тиража."""

from datetime import datetime, timedelta
from typing import Any

from app.engine.planner.base import TIMER_MARGIN
from app.engine.planner.decide import decide, lottery_params
from app.engine.planner.types import Wait
from app.engine.settings import Settings
from app.engine.state.model import BusyState, GorbushkaState, LotteryState, Obs
from tests.engine.planner.test_obligations import act, msk, only, state, verdicts

CUR = ("money", "knowledge", "raw", "details")
FULL = dict(zip(CUR, (10, 7, 7, 7), strict=True))
PRICES = dict(zip(CUR, (30, 4, 4, 8), strict=True))
EVENING = msk(19, 30)
PARAMS = {
    "reserve": 0,
    **{f"tickets_{c}": "max" for c in CUR},
    **{f"keep_{c}": 0 for c in CUR},
}


def lottery(at: datetime, **over: Any) -> Obs[LotteryState]:
    fields: dict[str, Any] = {
        "draw": 3286,
        "until": msk(21, 7),
        "bought": dict(FULL),
        "limits": dict(FULL),
        "prices": dict(PRICES),
    }
    fields.update(over)
    return Obs(value=LotteryState(**fields), at=at)


def cfg(*features: str, **lottery_section: Any) -> Settings:
    """Лотерея без сна: резерв на отель проверяется отдельно."""
    base = only("lottery", *features, lottery=lottery_section)
    return base.model_copy(update={"features": base.features.model_copy(update={"sleep": False})})


def test_waits_for_draw_start() -> None:
    now = msk(18, 30)
    decision = decide(state(now), cfg(), now)
    assert decision == Wait(msk(19, 17) + TIMER_MARGIN, "lottery_open", decision.candidates)


def test_unknown_or_old_draw_reads_screen() -> None:
    assert act(decide(state(EVENING), cfg(), EVENING)) == ("lottery_buy", PARAMS)
    # Снимок до 19:17 — прошлого тиража.
    yesterday = state(EVENING, lottery=lottery(msk(20, 0, day=25), bought=dict.fromkeys(CUR, 0)))
    decision = decide(yesterday, cfg(), EVENING)
    assert act(decision)[0] == "lottery_buy" and decision.reason == "lottery_unknown"
    doubtful = lottery(EVENING).model_copy(update={"src": "doubtful"})
    assert decide(state(EVENING, lottery=doubtful), cfg(), EVENING).reason == "lottery_unknown"
    unknown = lottery(EVENING, bought=None)
    assert decide(state(EVENING, lottery=unknown), cfg(), EVENING).reason == "lottery_unknown"


def test_target_reached_waits_for_next_draw() -> None:
    decision = decide(state(EVENING, lottery=lottery(EVENING)), cfg(), EVENING)
    assert isinstance(decision, Wait) and "lottery_buy" not in verdicts(decision)


def test_missing_affordable_currency_buys() -> None:
    part = lottery(EVENING, bought={**FULL, "money": 4, "details": 0})
    decision = decide(state(EVENING, lottery=part), cfg(), EVENING)
    assert act(decision) == ("lottery_buy", PARAMS)
    assert decision.reason == "lottery money,details"


def test_tickets_setting_caps_goal() -> None:
    part = lottery(EVENING, bought={**FULL, "money": 3})
    decision = decide(state(EVENING, lottery=part), cfg(tickets={"money": 3}), EVENING)
    assert isinstance(decision, Wait)
    more = decide(state(EVENING, lottery=part), cfg(tickets={"money": 20}), EVENING)
    assert act(more)[1]["tickets_money"] == 20


def test_keep_and_reserves_limit_money() -> None:
    part = lottery(EVENING, bought={**FULL, "money": 0})
    poor = state(EVENING, lottery=part, money=20)
    assert verdicts(decide(poor, cfg(), EVENING))["lottery_buy"] == "cant_afford"
    kept = decide(state(EVENING, lottery=part, money=500), cfg(keep={"money": 480}), EVENING)
    assert verdicts(kept)["lottery_buy"] == "cant_afford"
    # Резерв на билет Горбушки: из 140 на лотерею свободно 20.
    ticket = GorbushkaState(state="need_ticket")
    reserved = state(EVENING, lottery=part, money=140, gorbushka=ticket)
    decision = decide(reserved, cfg(), EVENING)
    assert verdicts(decision)["lottery_buy"] == "cant_afford"
    rich = state(EVENING, lottery=part, money=500, gorbushka=ticket)
    decision = decide(rich, cfg(), EVENING)
    assert act(decision) == ("lottery_buy", {**PARAMS, "reserve": 120})


def test_manual_run_gets_planner_params() -> None:
    ticket = GorbushkaState(state="need_ticket")
    reserved = state(EVENING, money=500, gorbushka=ticket)
    settings = cfg(tickets={"raw": 2}, keep={"money": 50})
    expected = {**PARAMS, "tickets_raw": 2, "keep_money": 50, "reserve": 120}
    assert lottery_params(reserved, settings, EVENING) == expected
    assert act(decide(reserved, settings, EVENING)) == ("lottery_buy", expected)


def test_hotel_reserve_before_night_sleep() -> None:
    # 19:30: до ночного сна в 22:05 меньше трёх часов, спать — в отеле (3 × 70 уровень = 210).
    part = lottery(EVENING, bought={**FULL, "money": 0})
    with_sleep = only("lottery")
    rich = decide(state(EVENING, lottery=part, money=500), with_sleep, EVENING)
    assert act(rich) == ("lottery_buy", {**PARAMS, "reserve": 210})
    poor = decide(state(EVENING, lottery=part, money=230), with_sleep, EVENING)
    assert verdicts(poor)["lottery_buy"] == "cant_afford"


def test_short_currency_needs_new_observation_not_screen() -> None:
    # Нехватка 💵 при 90, наблюдение денег устарело: не экран лотереи, а сначала профиль.
    part = lottery(EVENING, bought={**FULL, "money": 3}, short={"money": 90})
    old = Obs(value=500, at=EVENING - timedelta(hours=1))
    decision = decide(state(EVENING, lottery=part, money=old), cfg(), EVENING)
    assert act(decision) == ("refresh", {"source": "profile"})
    assert verdicts(decision)["lottery_buy"] == "stale:money"
    # Профиль недавно обновляли: ждём лимита рефреша, лотерею не открываем.
    recent = {"profile": EVENING - timedelta(seconds=30)}
    waiting = decide(state(EVENING, lottery=part, money=old), cfg(), EVENING, last_refresh=recent)
    assert isinstance(waiting, Wait) and waiting.reason == "refresh:profile"
    # Валюта без нехватки с устаревшим ресурсом — экран лотереи сам его покажет.
    unknown = lottery(EVENING, bought={**FULL, "details": 0})
    stale_details = Obs(value=5, at=EVENING - timedelta(hours=1))
    other = decide(state(EVENING, lottery=unknown, details=stale_details), cfg(), EVENING)
    assert act(other)[0] == "lottery_buy"


def test_short_currency_waits_for_more() -> None:
    part = lottery(EVENING, bought={**FULL, "money": 3}, short={"money": 90})
    same = decide(state(EVENING, lottery=part, money=90), cfg(), EVENING)
    assert verdicts(same)["lottery_buy"] == "cant_afford"
    grew = decide(state(EVENING, lottery=part, money=120), cfg(), EVENING)
    assert act(grew)[0] == "lottery_buy"


def test_last_start_and_after_sale() -> None:
    part = lottery(msk(21, 0), bought={**FULL, "money": 0})
    # Граница включительна: старт ровно в 21:05 ещё разрешён.
    for now in (msk(21, 4), msk(21, 5)):
        assert act(decide(state(now, lottery=part), cfg(), now))[0] == "lottery_buy"
    for now in (msk(21, 5) + timedelta(seconds=1), msk(21, 30), msk(23, 0)):
        decision = decide(state(now, lottery=part), cfg(), now)
        assert "lottery_buy" not in verdicts(decision)


def test_buys_during_deed_but_not_asleep() -> None:
    part = lottery(EVENING, bought={**FULL, "money": 0})
    working = BusyState(activity="harvest", until=EVENING + timedelta(minutes=4))
    decision = decide(state(EVENING, lottery=part, busy=working), cfg(), EVENING)
    assert act(decision)[0] == "lottery_buy"
    asleep = BusyState(activity="sleep_bridge", until=EVENING + timedelta(hours=5))
    decision = decide(state(EVENING, lottery=part, busy=asleep), cfg(), EVENING)
    assert "lottery_buy" not in verdicts(decision)


def test_feature_off_is_silent() -> None:
    decision = decide(state(EVENING), only(), EVENING)
    assert isinstance(decision, Wait) and "lottery_buy" not in verdicts(decision)
