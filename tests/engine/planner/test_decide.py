from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.engine.gametime import tasks_day
from app.engine.planner.base import READY_SLACK, TIMER_MARGIN
from app.engine.planner.decide import decide
from app.engine.planner.types import Act, Candidate, Decision, Wait
from app.engine.scenarios.registry import CERTIFIED
from app.engine.settings import Settings
from app.engine.state.model import (
    BusyState,
    CharacterState,
    FoodStockState,
    GorbushkaState,
    Obs,
    PriceState,
    TeamTask,
)

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
# Механики календаря фазы 4 проверяются в test_obligations.py; здесь — слой 2.
QUIET = {
    "stocks_dump": False,
    "factory": False,
    "bulls": False,
    "tangerine": False,
    "smoothie": False,
    "metro": False,
}


def config(data: dict[str, Any]) -> Settings:
    return Settings.model_validate({**data, "features": {**QUIET, **data.get("features", {})}})


BASE = config({})


def m(minutes: float) -> datetime:
    return NOW + timedelta(minutes=minutes)


def w(minutes: float) -> datetime:
    """Пробуждение по таймеру: с запасом на секундную точность игровых таймеров."""
    return m(minutes) + TIMER_MARGIN


def r(minutes: float) -> datetime:
    """Пробуждение по кулдауну с экрана: ещё минута на округление вниз."""
    return w(minutes) + READY_SLACK


SECOND = timedelta(seconds=1)


def obs(value: Any, age_min: float = 0) -> Obs[Any]:
    return Obs(value=value, at=m(-age_min))


FOOD = {
    "hotdog": FoodStockState(count=100, low=50, high=140),
    "pizza": FoodStockState(count=100, low=70, high=160),
    "burger": FoodStockState(count=100, low=90, high=180),
    "banana": FoodStockState(count=50, low=150, high=275),
}


def awake(at: datetime = NOW, **over: Any) -> CharacterState:
    """Состояние, снятое в `at` (по умолчанию — в `NOW`)."""

    def t(minutes: float) -> datetime:
        return at + timedelta(minutes=minutes)

    fields: dict[str, Any] = {
        "level": 70,
        "money": 500,
        "stamina": 100,
        "knowledge": 100,
        "raw": 0,
        "details": 1000,
        "motivation": 40,
        "motivation_next_at": t(30),
        "busy": None,
        "battle_at": at.replace(minute=0, second=0, microsecond=0) + timedelta(hours=3),
        "battle_target": "📯Pied Piper",
        "sleep_deadline": t(40 * 60),
        "sleep_allowed_at": t(-60),
        "levelup_pending": False,
        "books": 0,
        "book_ready_at": t(0),
        "cards": 0,
        "card_ready_at": t(0),
        "prizebox": False,
        "prizebox_ready_at": None,
        "food_stock": FOOD,
        "fastfood_ready_at": t(0),
        "containers_small": 0,
        "containers_medium": 0,
        "gorbushka": GorbushkaState(state="done", comeback_at=t(300)),
    }
    fields.update(over)
    return CharacterState(
        **{k: v if isinstance(v, Obs) else Obs(value=v, at=at) for k, v in fields.items()}
    )


def verdicts(decision: Decision) -> dict[str, str]:
    return {c.scenario: c.verdict for c in decision.candidates}


def act(decision: Decision) -> tuple[str, dict[str, Any]]:
    assert isinstance(decision, Act), decision
    return decision.scenario, decision.params


def test_idle_picks_best_deed_by_default_weights() -> None:
    decision = decide(awake(), BASE, NOW)
    assert act(decision) == ("deed:job", {})
    assert verdicts(decision) == {
        "deed:harvest": "ok",
        "deed:job": "chosen",
        "deed:learn": "ok",
        "deed:dconv": "ok",
    }


def test_experience_only_weights_pick_recycling() -> None:
    settings = config({"strategy": {"weight_money": 0, "weight_resources": 0}})
    assert act(decide(awake(), settings, NOW)) == ("deed:dconv", {})


def test_learned_average_replaces_prior() -> None:
    from app.engine.state.model import ActivityStat

    state = awake().model_copy(
        update={"activity_stats": {"harvest": ActivityStat(count=20, exp=900)}}
    )
    assert act(decide(state, BASE, NOW)) == ("deed:harvest", {})


def test_team_task_boosts_matching_resource() -> None:
    settings = config({"strategy": {"weight_team": 5}})
    today = tasks_day(NOW)
    state = awake(team_task=TeamTask(current=10, goal=360, resource="📚", day=today))
    assert act(decide(state, settings, NOW)) == ("deed:learn", {})
    # Вчерашнее командное задание не в счёт: оно могло смениться.
    yesterday = today - timedelta(days=1)
    old = awake(team_task=TeamTask(current=10, goal=360, resource="📚", day=yesterday))
    assert act(decide(old, settings, NOW)) == ("deed:job", {})


def test_busy_waits_until_free() -> None:
    state = awake(busy=BusyState(activity="job", until=m(2)))
    decision = decide(state, BASE, NOW)
    assert decision == Wait(w(2), "busy", ())


def test_deed_is_busy_within_timer_margin() -> None:
    state = awake(busy=obs(BusyState(activity="job", until=NOW - SECOND), age_min=3))
    assert decide(state, BASE, NOW) == Wait(NOW + 2 * SECOND, "busy", ())


def test_zero_left_busy_still_waits_margin() -> None:
    state = awake(busy=Obs(value=BusyState(activity="job", until=NOW - SECOND), at=NOW - SECOND))
    assert decide(state, BASE, NOW) == Wait(NOW + 2 * SECOND, "busy", ())


def test_sleeping_waits_without_candidates() -> None:
    state = awake(busy=BusyState(activity="sleep_hotel", until=m(300)), levelup_pending=True)
    assert decide(state, BASE, NOW) == Wait(w(300), "busy", ())


def test_expired_busy_counts_as_free() -> None:
    state = awake(busy=obs(BusyState(activity="job", until=m(-1)), age_min=3))
    assert act(decide(state, BASE, NOW)) == ("deed:job", {})


def test_levelup_first() -> None:
    state = awake(levelup_pending=True, books=5)
    assert act(decide(state, BASE, NOW)) == ("levelup", {})


def test_levelup_not_during_deed() -> None:
    state = awake(levelup_pending=True, busy=BusyState(activity="harvest", until=m(4)))
    decision = decide(state, BASE, NOW)
    assert isinstance(decision, Wait) and decision.until == w(4)
    assert verdicts(decision)["levelup"] == "busy"


def test_book_before_deeds_and_waits_for_cooldown() -> None:
    assert act(decide(awake(books=3), BASE, NOW)) == ("book", {})
    state = awake(books=3, book_ready_at=m(20), motivation=0)
    assert decide(state, BASE, NOW) == Wait(
        r(20), "book_ready", decide(state, BASE, NOW).candidates
    )


def test_book_not_ready_within_timer_margin() -> None:
    state = awake(books=3, book_ready_at=obs(NOW - SECOND, age_min=50), motivation=0)
    decision = decide(state, BASE, NOW)
    assert decision == Wait(NOW + READY_SLACK + 2 * SECOND, "book_ready", decision.candidates)


def test_book_not_while_busy() -> None:
    state = awake(books=3, busy=BusyState(activity="harvest", until=m(4)))
    decision = decide(state, BASE, NOW)
    assert isinstance(decision, Wait) and decision.until == w(4)
    assert verdicts(decision)["book"] == "busy"


@pytest.mark.parametrize(
    ("stamina", "food"),
    [(10, "hotdog"), (55, "pizza"), (80, "burger"), (95, None)],
)
def test_fastfood_kind_by_stamina(stamina: int, food: str | None) -> None:
    decision = decide(awake(stamina=stamina), BASE, NOW)
    if food is None:
        assert act(decision)[0] == "deed:job"
    else:
        assert act(decision) == ("fastfood", {"food": food})


def test_fastfood_order_and_banana_reserve() -> None:
    settings = config({"food": {"order": ["banana", "burger"]}})
    assert act(decide(awake(stamina=10), settings, NOW)) == ("fastfood", {"food": "burger"})
    rich = {**FOOD, "banana": FoodStockState(count=51, low=150, high=275)}
    state = awake(stamina=10, food_stock=rich)
    assert act(decide(state, settings, NOW)) == ("fastfood", {"food": "banana"})


def test_fastfood_during_deed_but_not_while_eating() -> None:
    during = awake(stamina=10, busy=BusyState(activity="harvest", until=m(4)))
    assert act(decide(during, BASE, NOW)) == ("fastfood", {"food": "hotdog"})
    eating = awake(stamina=10, busy=BusyState(activity="eat", until=m(4)))
    decision = decide(eating, BASE, NOW)
    assert isinstance(decision, Wait) and verdicts(decision)["fastfood"] == "eating"


def test_fastfood_cooldown_wakes() -> None:
    state = awake(stamina=10, fastfood_ready_at=m(12), motivation=0)
    decision = decide(state, BASE, NOW)
    assert isinstance(decision, Wait) and decision.until == r(12)


def test_containers_and_prizebox_during_deed() -> None:
    busy = BusyState(activity="harvest", until=m(4))
    assert act(decide(awake(containers_small=2), BASE, NOW)) == ("container_small", {})
    during = decide(awake(containers_small=2, busy=busy), BASE, NOW)
    assert during == Wait(w(4), "busy", during.candidates)
    assert verdicts(during)["container_small"] == "busy"
    assert act(decide(awake(prizebox=True, busy=busy), BASE, NOW)) == ("prizebox", {})
    locked = awake(prizebox=True, prizebox_ready_at=m(90), busy=busy)
    assert decide(locked, BASE, NOW).until == w(4)  # type: ignore[union-attr]


def test_uncertified_candidate_is_skipped() -> None:
    certified = frozenset({"container_small", "deed:job", "refresh"})
    decision = decide(awake(containers_medium=1), BASE, NOW, certified=certified)
    assert act(decision) == ("deed:job", {})
    assert verdicts(decision)["container_medium"] == "uncertified"


def test_feature_off_is_silent() -> None:
    settings = config({"features": {"books": False, "deeds": False}})
    decision = decide(awake(books=3), settings, NOW)
    assert isinstance(decision, Wait) and "book" not in verdicts(decision)


def test_gorbushka_fight_when_meeting() -> None:
    state = awake(gorbushka=GorbushkaState(state="meeting", won=1, total=4, fight_cost=1))
    assert act(decide(state, BASE, NOW)) == ("gorbushka", {"buy": False})


def test_gorbushka_buys_ticket_when_affordable() -> None:
    state = awake(gorbushka=GorbushkaState(state="need_ticket"))
    assert act(decide(state, BASE, NOW)) == ("gorbushka", {"buy": True})


def test_gorbushka_ticket_leaves_hotel_reserve() -> None:
    settings = config({"sleep": {"hotel_if_cash_after_reserve_ge": 50}})
    g = GorbushkaState(state="need_ticket")
    near = awake(money=200, gorbushka=g, sleep_deadline=m(4 * 60))
    decision = decide(near, settings, NOW)
    assert verdicts(decision)["gorbushka"] == "cant_afford"
    far = awake(money=200, gorbushka=g)
    assert act(decide(far, settings, NOW)) == ("gorbushka", {"buy": True})


def test_gorbushka_ticket_reserve_blocks_harvest() -> None:
    state = awake(money=140, knowledge=10, gorbushka=GorbushkaState(state="need_ticket"))
    settings = config({"strategy": {"weight_money": 0, "deeds": ["harvest", "job"]}})
    decision = decide(state, settings, NOW)
    assert act(decision) == ("deed:job", {})
    assert verdicts(decision) == {
        "gorbushka": "cant_afford",
        "deed:harvest": "no_money",
        "deed:job": "chosen",
    }


def test_gorbushka_waiting_reserves_motivation() -> None:
    g = GorbushkaState(state="waiting", won=1, total=4, next_fight_at=m(30), fight_cost=1)
    decision = decide(awake(motivation=1, gorbushka=g), BASE, NOW)
    assert isinstance(decision, Wait) and decision.until == w(30)
    assert verdicts(decision)["deed:job"] == "no_motivation"
    later = GorbushkaState(state="waiting", won=1, total=4, next_fight_at=m(90), fight_cost=1)
    assert act(decide(awake(motivation=1, gorbushka=later), BASE, NOW)) == ("deed:job", {})


def test_gorbushka_unknown_or_comeback_opens_screen() -> None:
    unknown = awake().model_copy(update={"gorbushka": None})
    assert act(decide(unknown, BASE, NOW)) == ("gorbushka", {"buy": False})
    back = GorbushkaState(state="done", comeback_at=m(-1))
    assert act(decide(awake(gorbushka=back), BASE, NOW)) == ("gorbushka", {"buy": False})


def test_stale_motivation_requests_profile() -> None:
    state = awake(motivation=obs(40, age_min=20))
    decision = decide(state, BASE, NOW)
    assert act(decision) == ("refresh", {"source": "profile"})
    assert verdicts(decision)["deeds"] == "stale:motivation"


def test_refresh_is_rate_limited() -> None:
    state = awake(motivation=obs(40, age_min=20))
    decision = decide(state, BASE, NOW, last_refresh={"profile": m(-1)})
    assert decision == Wait(w(1), "refresh:profile", decision.candidates)


def test_refresh_cooldown_is_per_source() -> None:
    state = awake(motivation=obs(40, age_min=20))
    held = decide(state, BASE, NOW, cooldowns={"refresh:profile": m(5)})
    assert held == Wait(w(5), "cooldown:refresh:profile", held.candidates)
    assert verdicts(held)["refresh"] == "cooldown"
    other = decide(state, BASE, NOW, cooldowns={"refresh:inventory": m(5)})
    assert act(other) == ("refresh", {"source": "profile"})


def test_unknown_busy_requests_profile_first() -> None:
    decision = decide(awake().model_copy(update={"busy": None}), BASE, NOW)
    assert act(decision) == ("refresh", {"source": "profile"})


@pytest.mark.parametrize(
    ("battle_in", "chosen"), [(14, "deed:harvest"), (10, "deed:job"), (7, None)]
)
def test_battle_window(battle_in: float, chosen: str | None) -> None:
    settings = config({"strategy": {"weight_money": 0, "deeds": ["harvest", "job"]}})
    battle = m(60)
    now = battle - timedelta(minutes=battle_in)
    decision = decide(awake(now, battle_at=battle), settings, now)
    if chosen is None:
        assert decision == Wait(w(61), "battle", decision.candidates)
    else:
        assert act(decision) == (chosen, {})


def test_no_motivation_waits_for_regen() -> None:
    decision = decide(awake(motivation=0), BASE, NOW)
    assert decision == Wait(w(30), "motivation", decision.candidates)


def test_cooldown_skips_scenario() -> None:
    decision = decide(awake(), BASE, NOW, cooldowns={"deed:job": m(5)})
    assert act(decision) == ("deed:dconv", {})
    assert verdicts(decision)["deed:job"] == "cooldown"


def test_deed_prices_from_screen() -> None:
    state = awake().model_copy(
        update={"prices": {"job": obs(PriceState(motivation=3, minutes=2))}}
    )
    assert act(decide(state, BASE, NOW)) == ("deed:dconv", {})


def test_sleep_near_deadline_passes_threshold_and_reserve() -> None:
    """Место выбирает сценарий по цене с экрана; планировщик отдаёт порог и резерв билета."""
    decision = decide(awake(sleep_deadline=m(60)), BASE, NOW)
    params = {"hours": 7, "hotel_threshold": None, "ticket_reserve": 0}
    assert act(decision) == ("sleep", params)
    settings = config({"sleep": {"hotel_if_cash_after_reserve_ge": 50}})
    configured = decide(awake(sleep_deadline=m(60)), settings, NOW)
    assert act(configured) == ("sleep", {**params, "hotel_threshold": 50})


def test_sleep_not_yet_allowed() -> None:
    state = awake(sleep_deadline=m(60), sleep_allowed_at=m(10), motivation=0)
    decision = decide(state, BASE, NOW)
    assert verdicts(decision)["sleep"] == "sleep_not_allowed"
    assert isinstance(decision, Wait) and decision.until == r(10)


def test_uncertified_sleep_leaves_deeds_but_not_past_deadline() -> None:
    certified = frozenset({"deed:harvest", "deed:job", "refresh"})
    settings = config({"strategy": {"deeds": ["harvest", "job"]}})
    decision = decide(awake(sleep_deadline=m(3)), settings, NOW, certified=certified)
    assert act(decision) == ("deed:job", {})
    assert verdicts(decision)["sleep"] == "uncertified"
    assert verdicts(decision)["deed:harvest"] == "sleep_deadline"


def test_wait_for_nearest_timer() -> None:
    settings = config({"features": {"deeds": False}})
    assert decide(awake(), settings, NOW) == Wait(w(300), "gorbushka_comeback", ())
    settings = config({"features": {"deeds": False, "gorbushka": False, "sleep": False}})
    assert decide(awake(), settings, NOW) == Wait(None, "no_timers", ())


def test_candidate_scores_recorded() -> None:
    decision = decide(awake(), BASE, NOW)
    job = next(c for c in decision.candidates if c.scenario == "deed:job")
    assert isinstance(job, Candidate) and job.score == pytest.approx(0.585 + 25.7 / 30 + 0.155)


def test_long_sleep_is_trusted_without_refresh() -> None:
    sleeping = obs(BusyState(activity="sleep_bridge", until=m(300)), age_min=180)
    decision = decide(awake(busy=sleeping), BASE, NOW)
    assert decision == Wait(w(300), "busy", ())


def test_doubtful_busy_requests_profile() -> None:
    doubtful = Obs(value=BusyState(activity="job", until=m(2)), at=NOW, src="doubtful")
    assert act(decide(awake(busy=doubtful), BASE, NOW)) == ("refresh", {"source": "profile"})


def test_regen_tick_makes_motivation_stale() -> None:
    state = awake(motivation=obs(0, age_min=5), motivation_next_at=m(-1))
    decision = decide(state, BASE, NOW)
    assert act(decision) == ("refresh", {"source": "profile"})
    assert verdicts(decision)["deeds"] == "stale:motivation"
    just = awake(motivation=obs(0, age_min=5), motivation_next_at=NOW - SECOND)
    decision = decide(just, BASE, NOW)
    assert decision == Wait(NOW + 2 * SECOND, "motivation", decision.candidates)


def test_past_battle_requests_profile() -> None:
    now = m(5)
    decision = decide(awake(now, battle_at=NOW), BASE, now)
    assert act(decision) == ("refresh", {"source": "profile"})
    assert verdicts(decision)["battle_target"] == "stale:battle_at"


def test_stale_price_is_taken_conservatively() -> None:
    cheap_learn = {"learn": obs(PriceState(motivation=1, minutes=4))}
    fresh = awake().model_copy(update={"prices": cheap_learn})
    assert act(decide(fresh, BASE, NOW)) == ("deed:learn", {})
    old = {"learn": obs(PriceState(motivation=1, minutes=4), age_min=8 * 24 * 60)}
    stale = awake().model_copy(update={"prices": old})
    assert act(decide(stale, BASE, NOW)) == ("deed:job", {})


def test_hotel_money_reserved_before_sleep_window() -> None:
    settings = config({"strategy": {"weight_money": 0, "deeds": ["harvest", "job"]}})
    near = decide(awake(money=230, sleep_deadline=m(4 * 60)), settings, NOW)
    assert act(near) == ("deed:job", {})
    assert verdicts(near)["deed:harvest"] == "no_money"
    far = decide(awake(money=230), settings, NOW)
    assert act(far) == ("deed:harvest", {})
    # Несертифицированный сон в live не исполнится — деньги на отель не держим.
    certified = frozenset({"deed:harvest", "deed:job", "refresh"})
    live = decide(awake(money=230, sleep_deadline=m(4 * 60)), settings, NOW, certified=certified)
    assert act(live) == ("deed:harvest", {})
    # Сертифицированный сон в live исполнится — деньги на отель держатся, как в dry_run.
    held = decide(awake(money=230, sleep_deadline=m(4 * 60)), settings, NOW, certified=CERTIFIED)
    assert act(held) == ("deed:job", {})


def test_screen_cooldown_waits_extra_minute_but_ready_screen_does_not() -> None:
    shown = awake(books=3, book_ready_at=obs(m(-0.5), age_min=5), motivation=0)
    decision = decide(shown, BASE, NOW)
    assert decision == Wait(r(-0.5), "book_ready", decision.candidates)
    ready = awake(books=3, book_ready_at=obs(m(-5), age_min=5))
    assert act(decide(ready, BASE, NOW)) == ("book", {})
