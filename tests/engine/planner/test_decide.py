from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.engine.planner.base import READY_SLACK, TIMER_MARGIN
from app.engine.planner.decide import decide, lottery_params
from app.engine.planner.types import Act, Candidate, Decision, Wait, Wakeup
from app.engine.scenarios.registry import CERTIFIED
from app.engine.settings import Settings
from app.engine.state.model import (
    ActivityStat,
    BusyState,
    CharacterState,
    FoodStockState,
    GorbushkaState,
    Obs,
    PriceState,
)

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
# Механики календаря фазы 4 проверяются в test_obligations.py, задания — в test_daily.py; здесь —
# слой 2.
QUIET = {
    "stocks_dump": False,
    "factory": False,
    "bulls": False,
    "tangerine": False,
    "smoothie": False,
    "metro": False,
    "daily_tasks": False,
    "lottery": False,
}


# Слой оценки проверяется без чередования основных дел (`strategy.focus` пуст); чередование —
# в тестах `focus_*` с настройками по умолчанию.
SCORE_ONLY = {"focus": []}


def config(data: dict[str, Any]) -> Settings:
    strategy = {**SCORE_ONLY, **data.get("strategy", {})}
    features = {**QUIET, **data.get("features", {})}
    return Settings.model_validate({**data, "strategy": strategy, "features": features})


BASE = config({})
FOCUS = Settings.model_validate({"features": QUIET})


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
    assert isinstance(decision, Act) and decision.reason.startswith("best score ")
    assert verdicts(decision) == {
        "deed:harvest": "ok",
        "deed:job": "chosen",
        "deed:learn": "ok",
        "deed:dconv": "ok",
        "deed:walk": "ok",
    }


def test_experience_only_weights_pick_recycling() -> None:
    settings = config({"strategy": {"weight_money": 0, "weight_resources": 0}})
    assert act(decide(awake(), settings, NOW)) == ("deed:dconv", {})


def test_learned_average_replaces_prior() -> None:
    state = awake().model_copy(
        update={"activity_stats": {"harvest": ActivityStat(count=20, exp=900)}}
    )
    assert act(decide(state, BASE, NOW)) == ("deed:harvest", {})


def test_focus_starts_with_first_main_deed() -> None:
    decision = decide(awake(), FOCUS, NOW)
    assert isinstance(decision, Act)
    assert (decision.scenario, decision.reason) == ("deed:harvest", "focus harvest (0 today)")
    focus = {c.scenario: c.params for c in decision.candidates if c.params}
    assert focus == {"deed:harvest": {"today": 0}, "deed:dconv": {"today": 0}}


@pytest.mark.parametrize(
    ("today", "chosen", "reason"),
    [
        ({"deed:harvest": 3, "deed:dconv": 2}, "deed:dconv", "focus dconv (2 today)"),
        ({"deed:harvest": 2, "deed:dconv": 2}, "deed:harvest", "focus harvest (2 today)"),
        ({"deed:dconv": 1}, "deed:harvest", "focus harvest (0 today)"),
        # Прочие дела в счётчике на чередование не влияют.
        ({"deed:harvest": 1, "deed:job": 9}, "deed:dconv", "focus dconv (0 today)"),
    ],
)
def test_focus_alternates_by_count_today(today: dict[str, int], chosen: str, reason: str) -> None:
    decision = decide(awake(), FOCUS, NOW, done_today=today)
    assert isinstance(decision, Act) and (decision.scenario, decision.reason) == (chosen, reason)


def test_unavailable_main_deed_passes_turn_to_other_main() -> None:
    # Добыче не хватает $30, переработке ($5 и 10⚙️) — хватает, даже если её сделано больше.
    decision = decide(awake(money=20), FOCUS, NOW, done_today={"deed:dconv": 5})
    assert act(decision) == ("deed:dconv", {})
    assert verdicts(decision)["deed:harvest"] == "no_money"
    cooled = decide(awake(), FOCUS, NOW, cooldowns={"deed:harvest": m(5)})
    assert act(cooled) == ("deed:dconv", {})


def test_no_main_deed_falls_back_to_best_score() -> None:
    decision = decide(awake(money=3, details=0), FOCUS, NOW)
    assert isinstance(decision, Act)
    assert (decision.scenario, decision.reason[:11]) == ("deed:job", "best score ")
    assert verdicts(decision)["deed:harvest"] == "no_money"
    assert verdicts(decision)["deed:dconv"] == "no_money"


# Добыча с прода: предметы крафта («Пуговица +1») в статистику не входят, оценка — только опыт
# минус цена 30💵: 192.6 / 200 − 1 < 0.
HARVEST_PROD = {"harvest": ActivityStat(count=3, exp=192.6)}


def test_main_deed_is_not_cut_by_score() -> None:
    state = awake().model_copy(update={"activity_stats": HARVEST_PROD})
    decision = decide(state, FOCUS, NOW, done_today={"deed:harvest": 2, "deed:dconv": 3})
    assert isinstance(decision, Act)
    assert (decision.scenario, decision.reason) == ("deed:harvest", "focus harvest (2 today)")
    harvest = next(c for c in decision.candidates if c.scenario == "deed:harvest")
    assert harvest.score is not None and harvest.score < 0
    # Очередь переработки: добыча доступна, но не выбрана.
    turn = decide(state, FOCUS, NOW, done_today={"deed:harvest": 3, "deed:dconv": 2})
    assert act(turn) == ("deed:dconv", {})
    assert verdicts(turn)["deed:harvest"] == "ok"


def test_deed_outside_focus_is_cut_by_score() -> None:
    state = awake().model_copy(update={"activity_stats": HARVEST_PROD})
    settings = Settings.model_validate({"features": QUIET, "strategy": {"focus": ["dconv"]}})
    decision = decide(state, settings, NOW, done_today={"deed:dconv": 3})
    assert act(decision) == ("deed:dconv", {})
    assert verdicts(decision)["deed:harvest"] == "no_value"


def test_main_deed_outside_allowed_deeds_is_ignored() -> None:
    settings = Settings.model_validate(
        {"features": QUIET, "strategy": {"deeds": ["job", "dconv"], "focus": ["harvest", "dconv"]}}
    )
    decision = decide(awake(), settings, NOW, done_today={"deed:dconv": 7})
    assert isinstance(decision, Act) and decision.reason == "focus dconv (7 today)"
    assert "deed:harvest" not in verdicts(decision)


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


def test_gorbushka_ticket_and_hotel_reserve() -> None:
    # Отель выбирается по деньгам сверх билета: либо хватает на оба, либо сон под мостом и резерва
    # на отель нет — билет его не съедает. Отель — 3💵 за уровень (210), порог ниже цены не в счёт.
    settings = config({"sleep": {"hotel_if_cash_after_reserve_ge": 50}})
    g = GorbushkaState(state="need_ticket")
    near = awake(money=200, gorbushka=g, sleep_deadline=m(4 * 60))
    assert act(decide(near, settings, NOW)) == ("gorbushka", {"buy": True})
    both = awake(money=330, gorbushka=g, sleep_deadline=m(4 * 60))
    assert act(decide(both, settings, NOW)) == ("gorbushka", {"buy": True})
    high = config({"sleep": {"hotel_if_cash_after_reserve_ge": 250}})
    held = awake(money=380, gorbushka=g, sleep_deadline=m(4 * 60))
    # 380 − 120 ≥ 250 (порог выше цены): отель, резерв — порог 250, на билет остаётся 130.
    assert act(decide(held, high, NOW)) == ("gorbushka", {"buy": True})


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
    # Следующая по оценке после работы — переработка (253💡 и 🔩5 за 1🔥 и $5).
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


def test_hotel_threshold_below_price_follows_sleep_scenario() -> None:
    # Сценарий сна берёт отель при 💵 ≥ max(цена, порог): порог ниже цены отель не удешевляет, и
    # резерва под отель, в который бот не ляжет, нет.
    settings = config(
        {
            "strategy": {"weight_money": 0, "deeds": ["harvest", "job"]},
            "sleep": {"hotel_if_cash_after_reserve_ge": 50},
        }
    )
    priced = {"hotel": obs(PriceState(money=210))}
    poor = awake(money=100, sleep_deadline=m(4 * 60)).model_copy(update={"prices": priced})
    assert act(decide(poor, settings, NOW)) == ("deed:harvest", {})
    rich = awake(money=230, sleep_deadline=m(4 * 60)).model_copy(update={"prices": priced})
    decision = decide(rich, settings, NOW)
    assert act(decision) == ("deed:job", {})
    assert verdicts(decision)["deed:harvest"] == "no_money"


def test_hotel_reserve_keeps_threshold_above_price() -> None:
    # Порог выше цены: сценарий сна возьмёт отель, только если 💵 ≥ 250, поэтому до сна
    # держится 250, а не цена 210 — иначе лотерея или дело потратили бы разницу и сон ушёл бы под
    # мост.
    settings = config({"sleep": {"hotel_if_cash_after_reserve_ge": 250}})
    priced = {"hotel": obs(PriceState(money=210))}
    state = awake(money=300, sleep_deadline=m(4 * 60)).model_copy(update={"prices": priced})
    assert lottery_params(state, settings, NOW)["reserve"] == 250
    far = awake(money=300).model_copy(update={"prices": priced})
    assert lottery_params(far, settings, NOW)["reserve"] == 0


def test_screen_cooldown_waits_extra_minute_but_ready_screen_does_not() -> None:
    shown = awake(books=3, book_ready_at=obs(m(-0.5), age_min=5), motivation=0)
    decision = decide(shown, BASE, NOW)
    assert decision == Wait(r(-0.5), "book_ready", decision.candidates)
    ready = awake(books=3, book_ready_at=obs(m(-5), age_min=5))
    assert act(decide(ready, BASE, NOW)) == ("book", {})


def test_wakeup_reason_is_kind_or_kind_and_key() -> None:
    assert Wakeup(NOW, "busy").reason == "busy"
    assert Wakeup(NOW, "cooldown", "refresh:profile").reason == "cooldown:refresh:profile"
    assert Wakeup(NOW, "refresh", "daily").reason == "refresh:daily"


def test_simultaneous_timers_wait_for_first_reason_by_name() -> None:
    # Таймеры в один момент: причина ожидания — первая по имени (как у прежних строк), а не
    # первая зарегистрированная (фастфуд проверяется раньше карты).
    at = obs(m(10), age_min=1)
    state = awake(stamina=10, cards=3, fastfood_ready_at=at, card_ready_at=at, motivation=0)
    decision = decide(state, BASE, NOW)
    assert decision == Wait(r(10), "card_ready", decision.candidates)
