from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.engine.planner.decide import decide
from app.engine.planner.types import Act, Candidate, Decision, Wait
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


def m(minutes: float) -> datetime:
    return NOW + timedelta(minutes=minutes)


def obs(value: Any, age_min: float = 0) -> Obs[Any]:
    return Obs(value=value, at=m(-age_min))


FOOD = {
    "hotdog": FoodStockState(count=100, low=50, high=140),
    "pizza": FoodStockState(count=100, low=70, high=160),
    "burger": FoodStockState(count=100, low=90, high=180),
    "banana": FoodStockState(count=50, low=150, high=275),
}


def awake(**over: Any) -> CharacterState:
    fields: dict[str, Any] = {
        "level": 70,
        "money": 500,
        "stamina": 100,
        "knowledge": 100,
        "raw": 0,
        "details": 1000,
        "motivation": 40,
        "motivation_next_at": m(30),
        "busy": None,
        "battle_at": m(180),
        "sleep_deadline": m(40 * 60),
        "sleep_allowed_at": m(-60),
        "levelup_pending": False,
        "books": 0,
        "book_ready_at": m(0),
        "cards": 0,
        "card_ready_at": m(0),
        "prizebox": False,
        "prizebox_ready_at": None,
        "food_stock": FOOD,
        "fastfood_ready_at": m(0),
        "containers_small": 0,
        "containers_medium": 0,
        "gorbushka": GorbushkaState(state="done", comeback_at=m(300)),
    }
    fields.update(over)
    return CharacterState(**{k: v if isinstance(v, Obs) else obs(v) for k, v in fields.items()})


def verdicts(decision: Decision) -> dict[str, str]:
    return {c.scenario: c.verdict for c in decision.candidates}


def act(decision: Decision) -> tuple[str, dict[str, Any]]:
    assert isinstance(decision, Act), decision
    return decision.scenario, decision.params


def test_idle_picks_best_deed_by_default_weights() -> None:
    decision = decide(awake(), Settings(), NOW)
    assert act(decision) == ("deed:job", {})
    assert verdicts(decision) == {
        "deed:harvest": "ok",
        "deed:job": "chosen",
        "deed:learn": "ok",
        "deed:dconv": "ok",
    }


def test_experience_only_weights_pick_recycling() -> None:
    settings = Settings.model_validate({"strategy": {"weight_money": 0, "weight_resources": 0}})
    assert act(decide(awake(), settings, NOW)) == ("deed:dconv", {})


def test_learned_average_replaces_prior() -> None:
    from app.engine.state.model import ActivityStat

    state = awake().model_copy(
        update={"activity_stats": {"harvest": ActivityStat(count=20, exp=900)}}
    )
    assert act(decide(state, Settings(), NOW)) == ("deed:harvest", {})


def test_team_task_boosts_matching_resource() -> None:
    settings = Settings.model_validate({"strategy": {"weight_team": 5}})
    state = awake(team_task=TeamTask(current=10, goal=360, resource="📚"))
    assert act(decide(state, settings, NOW)) == ("deed:learn", {})


def test_busy_waits_until_free() -> None:
    state = awake(busy=BusyState(activity="job", until=m(2)))
    decision = decide(state, Settings(), NOW)
    assert decision == Wait(m(2), "busy", ())


def test_sleeping_waits_without_candidates() -> None:
    state = awake(busy=BusyState(activity="sleep_hotel", until=m(300)), levelup_pending=True)
    assert decide(state, Settings(), NOW) == Wait(m(300), "busy", ())


def test_expired_busy_counts_as_free() -> None:
    state = awake(busy=obs(BusyState(activity="job", until=m(-1)), age_min=3))
    assert act(decide(state, Settings(), NOW)) == ("deed:job", {})


def test_levelup_first() -> None:
    state = awake(levelup_pending=True, books=5)
    assert act(decide(state, Settings(), NOW)) == ("levelup", {})


def test_book_before_deeds_and_waits_for_cooldown() -> None:
    assert act(decide(awake(books=3), Settings(), NOW)) == ("book", {})
    state = awake(books=3, book_ready_at=m(20), motivation=0)
    assert decide(state, Settings(), NOW) == Wait(
        m(20), "book_ready", decide(state, Settings(), NOW).candidates
    )


def test_book_not_while_busy() -> None:
    state = awake(books=3, busy=BusyState(activity="harvest", until=m(4)))
    decision = decide(state, Settings(), NOW)
    assert isinstance(decision, Wait) and decision.until == m(4)
    assert verdicts(decision)["book"] == "busy"


@pytest.mark.parametrize(
    ("stamina", "food"),
    [(10, "hotdog"), (55, "pizza"), (80, "burger"), (95, None)],
)
def test_fastfood_kind_by_stamina(stamina: int, food: str | None) -> None:
    decision = decide(awake(stamina=stamina), Settings(), NOW)
    if food is None:
        assert act(decision)[0] == "deed:job"
    else:
        assert act(decision) == ("fastfood", {"food": food})


def test_fastfood_order_and_banana_reserve() -> None:
    settings = Settings.model_validate({"food": {"order": ["banana", "burger"]}})
    assert act(decide(awake(stamina=10), settings, NOW)) == ("fastfood", {"food": "burger"})
    rich = {**FOOD, "banana": FoodStockState(count=51, low=150, high=275)}
    state = awake(stamina=10, food_stock=rich)
    assert act(decide(state, settings, NOW)) == ("fastfood", {"food": "banana"})


def test_fastfood_during_deed_but_not_while_eating() -> None:
    during = awake(stamina=10, busy=BusyState(activity="harvest", until=m(4)))
    assert act(decide(during, Settings(), NOW)) == ("fastfood", {"food": "hotdog"})
    eating = awake(stamina=10, busy=BusyState(activity="eat", until=m(4)))
    decision = decide(eating, Settings(), NOW)
    assert isinstance(decision, Wait) and verdicts(decision)["fastfood"] == "eating"


def test_fastfood_cooldown_wakes() -> None:
    state = awake(stamina=10, fastfood_ready_at=m(12), motivation=0)
    decision = decide(state, Settings(), NOW)
    assert isinstance(decision, Wait) and decision.until == m(12)


def test_containers_and_prizebox_during_deed() -> None:
    busy = BusyState(activity="harvest", until=m(4))
    assert act(decide(awake(containers_small=2, busy=busy), Settings(), NOW)) == (
        "container_small",
        {},
    )
    assert act(decide(awake(prizebox=True, busy=busy), Settings(), NOW)) == ("prizebox", {})
    locked = awake(prizebox=True, prizebox_ready_at=m(90), busy=busy)
    assert decide(locked, Settings(), NOW).until == m(4)  # type: ignore[union-attr]


def test_uncertified_candidate_is_skipped() -> None:
    certified = frozenset({"container_small", "deed:job", "refresh"})
    decision = decide(awake(containers_medium=1), Settings(), NOW, certified=certified)
    assert act(decision) == ("deed:job", {})
    assert verdicts(decision)["container_medium"] == "uncertified"


def test_feature_off_is_silent() -> None:
    settings = Settings.model_validate({"features": {"books": False, "deeds": False}})
    decision = decide(awake(books=3), settings, NOW)
    assert isinstance(decision, Wait) and "book" not in verdicts(decision)


def test_gorbushka_fight_when_meeting() -> None:
    state = awake(gorbushka=GorbushkaState(state="meeting", won=1, total=4, fight_cost=1))
    assert act(decide(state, Settings(), NOW)) == ("gorbushka", {"buy": False})


def test_gorbushka_buys_ticket_when_affordable() -> None:
    state = awake(gorbushka=GorbushkaState(state="need_ticket"))
    assert act(decide(state, Settings(), NOW)) == ("gorbushka", {"buy": True})


def test_gorbushka_ticket_reserve_blocks_harvest() -> None:
    state = awake(money=140, knowledge=10, gorbushka=GorbushkaState(state="need_ticket"))
    settings = Settings.model_validate(
        {"strategy": {"weight_money": 0, "deeds": ["harvest", "job"]}}
    )
    decision = decide(state, settings, NOW)
    assert act(decision) == ("deed:job", {})
    assert verdicts(decision) == {
        "gorbushka": "cant_afford",
        "deed:harvest": "no_money",
        "deed:job": "chosen",
    }


def test_gorbushka_waiting_reserves_motivation() -> None:
    g = GorbushkaState(state="waiting", won=1, total=4, next_fight_at=m(30), fight_cost=1)
    decision = decide(awake(motivation=1, gorbushka=g), Settings(), NOW)
    assert isinstance(decision, Wait) and decision.until == m(30)
    assert verdicts(decision)["deed:job"] == "no_motivation"
    later = GorbushkaState(state="waiting", won=1, total=4, next_fight_at=m(90), fight_cost=1)
    assert act(decide(awake(motivation=1, gorbushka=later), Settings(), NOW)) == ("deed:job", {})


def test_gorbushka_unknown_or_comeback_opens_screen() -> None:
    unknown = awake().model_copy(update={"gorbushka": None})
    assert act(decide(unknown, Settings(), NOW)) == ("gorbushka", {"buy": False})
    back = GorbushkaState(state="done", comeback_at=m(-1))
    assert act(decide(awake(gorbushka=back), Settings(), NOW)) == ("gorbushka", {"buy": False})


def test_stale_motivation_requests_profile() -> None:
    state = awake(motivation=obs(40, age_min=20))
    decision = decide(state, Settings(), NOW)
    assert act(decision) == ("refresh", {"source": "profile"})
    assert verdicts(decision)["deeds"] == "stale:motivation"


def test_refresh_is_rate_limited() -> None:
    state = awake(motivation=obs(40, age_min=20))
    decision = decide(state, Settings(), NOW, last_refresh={"profile": m(-1)})
    assert decision == Wait(m(1), "refresh:profile", decision.candidates)


def test_unknown_busy_requests_profile_first() -> None:
    decision = decide(awake().model_copy(update={"busy": None}), Settings(), NOW)
    assert act(decision) == ("refresh", {"source": "profile"})


@pytest.mark.parametrize(
    ("battle_in", "chosen"), [(14, "deed:harvest"), (10, "deed:job"), (7, None)]
)
def test_battle_window(battle_in: float, chosen: str | None) -> None:
    settings = Settings.model_validate(
        {"strategy": {"weight_money": 0, "deeds": ["harvest", "job"]}}
    )
    decision = decide(awake(battle_at=m(battle_in)), settings, NOW)
    if chosen is None:
        assert decision == Wait(m(battle_in + 1), "battle", decision.candidates)
    else:
        assert act(decision) == (chosen, {})


def test_no_motivation_waits_for_regen() -> None:
    decision = decide(awake(motivation=0), Settings(), NOW)
    assert decision == Wait(m(30), "motivation", decision.candidates)


def test_cooldown_skips_scenario() -> None:
    decision = decide(awake(), Settings(), NOW, cooldowns={"deed:job": m(5)})
    assert act(decision) == ("deed:dconv", {})
    assert verdicts(decision)["deed:job"] == "cooldown"


def test_deed_prices_from_screen() -> None:
    state = awake().model_copy(
        update={"prices": {"job": obs(PriceState(motivation=3, minutes=2))}}
    )
    assert act(decide(state, Settings(), NOW)) == ("deed:dconv", {})


def test_sleep_near_deadline_with_hotel() -> None:
    decision = decide(awake(sleep_deadline=m(60)), Settings(), NOW)
    assert act(decision) == ("sleep", {"hours": 7, "hotel": True})
    poor = decide(awake(sleep_deadline=m(60), money=100), Settings(), NOW)
    assert act(poor) == ("sleep", {"hours": 7, "hotel": False})


def test_sleep_not_yet_allowed() -> None:
    state = awake(sleep_deadline=m(60), sleep_allowed_at=m(10), motivation=0)
    decision = decide(state, Settings(), NOW)
    assert verdicts(decision)["sleep"] == "sleep_not_allowed"
    assert isinstance(decision, Wait) and decision.until == m(10)


def test_uncertified_sleep_leaves_deeds_but_not_past_deadline() -> None:
    certified = frozenset({"deed:harvest", "deed:job", "refresh"})
    settings = Settings.model_validate({"strategy": {"deeds": ["harvest", "job"]}})
    decision = decide(awake(sleep_deadline=m(3)), settings, NOW, certified=certified)
    assert act(decision) == ("deed:job", {})
    assert verdicts(decision)["sleep"] == "uncertified"
    assert verdicts(decision)["deed:harvest"] == "sleep_deadline"


def test_wait_for_nearest_timer() -> None:
    settings = Settings.model_validate({"features": {"deeds": False}})
    assert decide(awake(), settings, NOW) == Wait(m(300), "gorbushka_comeback", ())
    settings = Settings.model_validate(
        {"features": {"deeds": False, "gorbushka": False, "sleep": False}}
    )
    assert decide(awake(), settings, NOW) == Wait(None, "no_timers", ())


def test_candidate_scores_recorded() -> None:
    decision = decide(awake(), Settings(), NOW)
    job = next(c for c in decision.candidates if c.scenario == "deed:job")
    assert isinstance(job, Candidate) and job.score == pytest.approx(0.585 + 25.7 / 30 + 0.155)


def test_long_sleep_is_trusted_without_refresh() -> None:
    sleeping = obs(BusyState(activity="sleep_bridge", until=m(300)), age_min=180)
    decision = decide(awake(busy=sleeping), Settings(), NOW)
    assert decision == Wait(m(300), "busy", ())


def test_doubtful_busy_requests_profile() -> None:
    doubtful = Obs(value=BusyState(activity="job", until=m(2)), at=NOW, src="doubtful")
    assert act(decide(awake(busy=doubtful), Settings(), NOW)) == ("refresh", {"source": "profile"})


def test_regen_tick_makes_motivation_stale() -> None:
    state = awake(motivation=obs(0, age_min=5), motivation_next_at=m(-1))
    decision = decide(state, Settings(), NOW)
    assert act(decision) == ("refresh", {"source": "profile"})
    assert verdicts(decision)["deeds"] == "stale:motivation"


def test_past_battle_requests_profile_before_deeds() -> None:
    decision = decide(awake(battle_at=m(-5)), Settings(), NOW)
    assert act(decision) == ("refresh", {"source": "profile"})
    assert verdicts(decision)["deeds"] == "stale:battle_at"


def test_stale_price_is_taken_conservatively() -> None:
    cheap_learn = {"learn": obs(PriceState(motivation=1, minutes=4))}
    fresh = awake().model_copy(update={"prices": cheap_learn})
    assert act(decide(fresh, Settings(), NOW)) == ("deed:learn", {})
    old = {"learn": obs(PriceState(motivation=1, minutes=4), age_min=8 * 24 * 60)}
    stale = awake().model_copy(update={"prices": old})
    assert act(decide(stale, Settings(), NOW)) == ("deed:job", {})


def test_hotel_money_reserved_before_sleep_window() -> None:
    settings = Settings.model_validate(
        {"strategy": {"weight_money": 0, "deeds": ["harvest", "job"]}}
    )
    near = decide(awake(money=230, sleep_deadline=m(4 * 60)), settings, NOW)
    assert act(near) == ("deed:job", {})
    assert verdicts(near)["deed:harvest"] == "no_money"
    far = decide(awake(money=230), settings, NOW)
    assert act(far) == ("deed:harvest", {})
