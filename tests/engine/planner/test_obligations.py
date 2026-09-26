from datetime import datetime, timedelta
from typing import Any

import pytest

from app.engine.gametime import MSK
from app.engine.planner.base import READY_SLACK, TIMER_MARGIN, battle_hour
from app.engine.planner.decide import decide
from app.engine.planner.types import Act, Decision, Wait
from app.engine.settings import Settings
from app.engine.state.model import (
    BusyState,
    CharacterState,
    FoodStockState,
    GorbushkaState,
    Obs,
    SmoothieRecipeState,
    StockLimits,
    TargetSet,
)

PHASE4 = ("stocks_dump", "factory", "bulls", "tangerine", "smoothie", "metro")
FOOD = {"hotdog": FoodStockState(count=100, low=50, high=140)}
LIMITS = StockLimits(min_buy=11, max_sell=80, reserve=100, open_hour=8, close_hour=22)
QUOTES = {"piper": 10, "hooli": 10, "stark": 31, "umbrl": 100, "wayne": 10, "bmesa": 10}
RECIPE = SmoothieRecipeState(recipe="🍇🥕🥕🍋🍅", bonus="💡Получаешь на +50% больше Опыта")
TANGERINE = {"chat": -1001377961602, "reply_to": 927136}


def msk(hour: int, minute: int = 0, day: int = 26) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=MSK)


def only(*features: str, **sections: Any) -> Settings:
    """Настройки, где из механик фазы 4 включены только `features`."""
    flags = {name: name in features for name in PHASE4}
    return Settings.model_validate({**sections, "features": flags})


def state(now: datetime, **over: Any) -> CharacterState:
    def at(minutes: float) -> datetime:
        return now + timedelta(minutes=minutes)

    fields: dict[str, Any] = {
        "level": 70,
        "money": 500,
        "stamina": 100,
        "knowledge": 100,
        "raw": 0,
        "details": 1000,
        "motivation": 0,
        "motivation_next_at": at(24 * 60),
        "busy": None,
        # Битвы — в начале часа.
        "battle_at": now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=9),
        "battle_target": "📯Pied Piper",
        "sleep_deadline": at(40 * 60),
        "sleep_allowed_at": at(-60),
        "levelup_pending": False,
        "books": 0,
        "book_ready_at": at(0),
        "cards": 0,
        "card_ready_at": at(0),
        "prizebox": False,
        "prizebox_ready_at": None,
        "food_stock": FOOD,
        "fastfood_ready_at": at(0),
        "containers_small": 0,
        "containers_medium": 0,
        "gorbushka": GorbushkaState(state="done", comeback_at=at(30 * 60)),
    }
    fields.update(over)
    return CharacterState(
        **{k: v if isinstance(v, Obs) else Obs(value=v, at=now) for k, v in fields.items()}
    )


def act(decision: Decision) -> tuple[str, dict[str, Any]]:
    assert isinstance(decision, Act), decision
    return decision.scenario, decision.params


def verdicts(decision: Decision) -> dict[str, str]:
    return {c.scenario: c.verdict for c in decision.candidates}


# --- битва

NOON = msk(12)


def test_target_set_when_profile_shows_none() -> None:
    decision = decide(state(NOON, battle_target=None), only(), NOON)
    assert act(decision) == ("battle_target", {"target": "📯Pied Piper"})


def test_target_override_for_battle_hour() -> None:
    battle = msk(13)
    cfg = only(battle={"overrides": {13: "🛡Защита"}})
    decision = decide(state(NOON, battle_at=battle, battle_target=None), cfg, NOON)
    assert act(decision) == ("battle_target", {"target": "🛡Защита"})


def test_invisible_defense_trusted_by_battle_it_was_set_for() -> None:
    battle = msk(13)
    cfg = only(battle={"target": "🛡Защита"})
    defense = TargetSet(target="🛡Защита", battle_at=battle)
    ready = state(NOON, battle_at=battle, battle_target=None, battle_target_set=defense)
    assert isinstance(decide(ready, cfg, NOON), Wait)
    old = defense.model_copy(update={"battle_at": msk(22, day=25)})
    stale = state(NOON, battle_at=battle, battle_target=None, battle_target_set=old)
    assert act(decide(stale, cfg, NOON))[0] == "battle_target"
    changed = only(battle={"target": "📯Pied Piper"})
    decision = decide(ready, changed, NOON)
    assert act(decision) == ("battle_target", {"target": "📯Pied Piper"})


def test_profile_with_other_target_is_reset() -> None:
    decision = decide(state(NOON, battle_target="🤖Hooli"), only(), NOON)
    assert act(decision) == ("battle_target", {"target": "📯Pied Piper"})


def test_target_old_profile_does_not_count() -> None:
    before = Obs(value="📯Pied Piper", at=NOON - timedelta(hours=10))
    decision = decide(state(NOON, battle_target=before), only(), NOON)
    assert act(decision)[0] == "battle_target"


def test_target_set_while_sleeping() -> None:
    sleeping = BusyState(activity="sleep_hotel", until=NOON + timedelta(hours=3))
    decision = decide(state(NOON, busy=sleeping, battle_target=None), only(), NOON)
    assert act(decision) == ("battle_target", {"target": "📯Pied Piper"})


def test_no_target_in_last_minute() -> None:
    now = msk(13) - timedelta(seconds=50)
    soon = state(now, battle_at=msk(13), battle_target=None)
    decision = decide(soon, only(), now)
    assert "battle_target" not in verdicts(decision)


@pytest.mark.parametrize(
    "seen",
    [
        datetime(2026, 9, 26, 12, 59, 6, tzinfo=MSK),
        datetime(2026, 9, 26, 13, 0, 0, 400000, tzinfo=MSK),
        msk(12, 5),
        msk(13),
    ],
)
def test_battle_is_on_the_hour(seen: datetime) -> None:
    assert battle_hour(seen) == msk(13)


def test_hourly_countdown_seen_early_in_the_hour() -> None:
    # Профиль 3549074: в 10:01:49 «Битва через 1д. 23ч.» — битва через двое суток в 10:00.
    seen = datetime(2026, 3, 22, 10, 1, 49, tzinfo=MSK)
    battle = datetime(2026, 3, 24, 10, 0, tzinfo=MSK)
    assert battle_hour(seen + timedelta(hours=47), seen) == battle
    # Отсчёт с минутами может указать на секунду позже начала часа.
    shown = datetime(2026, 9, 26, 12, 51, 31, tzinfo=MSK)
    assert battle_hour(msk(13) + timedelta(seconds=1), shown) == msk(13)
    cfg = only(battle={"overrides": {10: "🛡Защита"}})
    holiday = state(seen, battle_at=seen + timedelta(hours=47), battle_target=None)
    assert act(decide(holiday, cfg, seen)) == ("battle_target", {"target": "🛡Защита"})


def test_override_by_hour_of_rounded_down_countdown() -> None:
    # Профиль в 12:00:06 показал «Битва через 59 мин.»: битва — в 13:00, а не в 12-м часу.
    now = datetime(2026, 9, 26, 12, 0, 6, tzinfo=MSK)
    seen = datetime(2026, 9, 26, 12, 59, 6, tzinfo=MSK)
    cfg = only(battle={"overrides": {13: "🛡Защита"}})
    decision = decide(state(now, battle_at=seen, battle_target=None), cfg, now)
    assert act(decision) == ("battle_target", {"target": "🛡Защита"})


def test_holiday_target_matches_battle_of_other_precision() -> None:
    # В каникулы профиль округляет отсчёт до часа, ответ на выбор цели — точнее.
    now = msk(10, 5)
    cfg = only(battle={"target": "🛡Защита"})
    defense = TargetSet(target="🛡Защита", battle_at=msk(10, 47) + timedelta(days=9))
    holiday = state(
        now, battle_at=now + timedelta(days=9), battle_target=None, battle_target_set=defense
    )
    decision = decide(holiday, cfg, now)
    assert "battle_target" not in verdicts(decision)
    # Ответ на выбор цели, полученный в первые минуты часа, — тоже о битве в 11:00.
    early = datetime(2026, 9, 26, 10, 1, 30, tzinfo=MSK)
    answer = TargetSet(target="🛡Защита", battle_at=early + timedelta(days=9))
    holiday = state(
        now,
        battle_at=now + timedelta(days=9),
        battle_target=None,
        battle_target_set=Obs(value=answer, at=early),
    )
    assert "battle_target" not in verdicts(decide(holiday, cfg, now))


def test_zero_stamina_before_battle_eats_when_no_fastfood() -> None:
    now = msk(12, 40)
    cfg = Settings.model_validate(
        {"features": {**{name: False for name in PHASE4}, "fastfood": False}}
    )
    hungry = state(now, stamina=0, battle_at=msk(13))
    assert act(decide(hungry, cfg, now)) == ("deed:eat", {})
    with_food = state(now, stamina=0, battle_at=msk(13))
    decision = decide(with_food, only(), now)
    assert act(decision) == ("fastfood", {"food": "hotdog"})
    assert isinstance(decision, Act) and decision.reason == "battle_stamina"


def test_battle_stamina_uses_fastfood_rules() -> None:
    now, battle = msk(12, 40), msk(13)
    bananas = {"banana": FoodStockState(count=10, low=150, high=275)}
    only_bananas = state(now, stamina=0, battle_at=battle, food_stock=bananas)
    assert act(decide(only_bananas, only(), now)) == ("deed:eat", {})
    late = state(now, stamina=0, battle_at=battle, fastfood_ready_at=battle)
    assert act(decide(late, only(), now)) == ("deed:eat", {})
    soon = state(now, stamina=0, battle_at=battle, fastfood_ready_at=now + timedelta(minutes=5))
    decision = decide(soon, only(), now)
    assert isinstance(decision, Wait) and decision.reason == "fastfood_ready"


def test_battle_stamina_refreshes_stale_food_before_paid_eat() -> None:
    now = msk(12, 40)
    old_food = Obs(value=FOOD, at=now - timedelta(hours=7))
    hungry = state(now, stamina=0, battle_at=msk(13), food_stock=old_food)
    decision = decide(hungry, only(), now)
    assert act(decision) == ("refresh", {"source": "food"})
    assert verdicts(decision)["battle_stamina"] == "stale:food_stock"


def test_battle_stamina_eat_only_if_done_before_battle() -> None:
    no_fastfood = Settings.model_validate(
        {"features": {**{name: False for name in PHASE4}, "fastfood": False}}
    )
    late = msk(12, 50)
    decision = decide(state(late, stamina=0, battle_at=msk(13)), no_fastfood, late)
    assert "deed:eat" not in verdicts(decision)
    in_time = msk(13) - timedelta(minutes=11)
    decision = decide(state(in_time, stamina=0, battle_at=msk(13)), no_fastfood, in_time)
    assert act(decision) == ("deed:eat", {})


def test_battle_stamina_no_eat_when_deeds_off() -> None:
    cfg = Settings.model_validate(
        {"features": {**{name: False for name in PHASE4}, "fastfood": False, "deeds": False}}
    )
    now = msk(12, 40)
    decision = decide(state(now, stamina=0, battle_at=msk(13)), cfg, now)
    assert "deed:eat" not in verdicts(decision)


def test_battle_feature_off_skips_target_and_stamina() -> None:
    now = msk(12, 40)
    hungry = state(now, stamina=0, battle_at=msk(13), battle_target=None)
    flags = {**{name: False for name in PHASE4}, "fastfood": False}
    on = Settings.model_validate({"features": flags})
    assert act(decide(hungry, on, now))[0] == "battle_target"
    off = Settings.model_validate({"features": {**flags, "battle": False}})
    decision = decide(hungry, off, now)
    assert {"battle_target", "deed:eat", "refresh"}.isdisjoint(verdicts(decision))
    past = state(now, battle_at=msk(12), battle_target=None)
    assert "battle_target" not in verdicts(decide(past, off, now))


# --- слив налички в акции

DUMP_BATTLE = msk(13)


def dumping(now: datetime, **over: Any) -> CharacterState:
    fields = {
        "money": 900,
        "battle_at": DUMP_BATTLE,
        "stock_quotes": QUOTES,
        "stock_limits": LIMITS,
        **over,
    }
    return state(now, **fields)


def test_dump_in_window_before_battle() -> None:
    now = msk(12, 50)
    decision = decide(dumping(now), only("stocks_dump"), now)
    assert act(decision) == ("stocks_dump", {"keep": 150 + 210, "margin": 5})


def test_dump_waits_for_window() -> None:
    now = msk(12, 30)
    decision = decide(dumping(now), only("stocks_dump"), now)
    assert isinstance(decision, Wait)
    assert decision.until == msk(12, 45) + TIMER_MARGIN


def test_dump_skipped_below_minimum_or_without_candidate() -> None:
    now = msk(12, 50)
    assert isinstance(decide(dumping(now, money=500), only("stocks_dump"), now), Wait)
    flat = dict.fromkeys(QUOTES, 10)
    decision = decide(dumping(now, stock_quotes=flat), only("stocks_dump"), now)
    assert verdicts(decision)["stocks_dump"] == "no_stock"


def test_dump_ignores_quotes_seen_before_window() -> None:
    now = msk(12, 50)
    flat = Obs(value=dict.fromkeys(QUOTES, 10), at=msk(10))
    decision = decide(dumping(now, stock_quotes=flat), only("stocks_dump"), now)
    assert act(decision) == ("stocks_dump", {"keep": 150 + 210, "margin": 5})


def test_dump_keeps_ticket_reserve() -> None:
    now = msk(12, 50)
    ticket = GorbushkaState(state="need_ticket")
    decision = decide(dumping(now, money=900, gorbushka=ticket), only("stocks_dump"), now)
    assert act(decision) == ("stocks_dump", {"keep": 150 + 120 + 210, "margin": 5})


def test_dump_keeps_tonights_hotel_only_when_sleeping_in_hotel() -> None:
    now = msk(12, 50)
    bridge = only("stocks_dump", sleep={"hotel_if_cash_after_reserve_ge": 5000})
    decision = decide(dumping(now), bridge, now)
    assert act(decision) == ("stocks_dump", {"keep": 150, "margin": 5})
    rich_hotel = only("stocks_dump", sleep={"hotel_if_cash_after_reserve_ge": 500})
    decision = decide(dumping(now), rich_hotel, now)
    assert act(decision) == ("stocks_dump", {"keep": 150 + 500, "margin": 5})


def test_no_hotel_reserve_while_sleep_uncertified() -> None:
    now = msk(12, 50)
    rich_hotel = only("stocks_dump", sleep={"hotel_if_cash_after_reserve_ge": 500})
    certified = frozenset({"stocks_dump", "refresh"})
    decision = decide(dumping(now), rich_hotel, now, certified=certified)
    assert act(decision) == ("stocks_dump", {"keep": 150, "margin": 5})


def test_dump_checks_against_market_reserve() -> None:
    now = msk(12, 50)
    cfg = only(
        "stocks_dump",
        stocks={"cash_floor": 50},
        sleep={"hotel_if_cash_after_reserve_ge": 5000},
    )
    decision = decide(dumping(now, money=280), cfg, now)
    assert "stocks_dump" not in verdicts(decision)
    decision = decide(dumping(now, money=300), cfg, now)
    assert act(decision) == ("stocks_dump", {"keep": 50, "margin": 5})


def test_dump_not_when_market_closed() -> None:
    now = msk(22, 50)
    night = dumping(now, battle_at=msk(23))
    decision = decide(night, only("stocks_dump"), now)
    assert verdicts(decision)["stocks_dump"] == "market_closed"


# --- фабрика


def test_factory_signup_in_window() -> None:
    now = msk(18, 5)
    assert act(decide(state(now), only("factory"), now)) == ("factory_signup", {})


@pytest.mark.parametrize(
    "flags",
    [
        {"factory_signed": Obs(value=True, at=msk(18, 2))},
        {"factory_skip": Obs(value=True, at=msk(18, 1))},
        {"factory_won_at": msk(18, 30, day=25)},
    ],
)
def test_factory_not_needed(flags: dict[str, Any]) -> None:
    now = msk(18, 5)
    decision = decide(state(now, **flags), only("factory"), now)
    assert "factory_signup" not in verdicts(decision)


def test_factory_old_signup_or_old_win_do_not_count() -> None:
    now = msk(18, 5)
    yesterday = {"factory_signed": Obs(value=True, at=msk(18, 2, day=25))}
    assert act(decide(state(now, **yesterday), only("factory"), now))[0] == "factory_signup"
    old_win = {"factory_won_at": msk(18, 30, day=24)}
    assert act(decide(state(now, **old_win), only("factory"), now))[0] == "factory_signup"


def test_deeds_keep_signup_window_free() -> None:
    now = msk(17, 57)
    cfg = only("factory", strategy={"deeds": ["harvest", "job"]})
    decision = decide(state(now, motivation=40), cfg, now)
    assert act(decision) == ("deed:job", {})
    assert verdicts(decision)["deed:harvest"] == "factory_window"


def test_factory_waits_for_busy_then_after_close_forgets() -> None:
    now = msk(18, 5)
    busy = BusyState(activity="job", until=now + timedelta(minutes=1))
    decision = decide(state(now, busy=busy), only("factory"), now)
    assert verdicts(decision)["factory_signup"] == "busy"
    late = msk(18, 20)
    assert "factory_signup" not in verdicts(decide(state(late), only("factory"), late))


# --- биржевики

NIGHT = msk(23, 30)


def invited(now: datetime, age_min: float = 1, **over: Any) -> CharacterState:
    invite = Obs(value="join_fight_AaBH89kYd2J", at=now - timedelta(minutes=age_min))
    return state(now, bulls_invite=invite, **over)


def test_bulls_join_fresh_night_invite() -> None:
    decision = decide(invited(NIGHT), only("bulls"), NIGHT)
    assert act(decision) == ("bulls_join", {"code": "join_fight_AaBH89kYd2J"})


@pytest.mark.parametrize(
    ("now", "age", "over"),
    [
        (NIGHT, 5, {}),
        (msk(12), 1, {}),
        (NIGHT, 1, {"bulls_won_at": msk(22, 40)}),
        (msk(8, 1), 2, {}),
    ],
)
def test_bulls_ignored(now: datetime, age: float, over: dict[str, Any]) -> None:
    decision = decide(invited(now, age, **over), only("bulls"), now)
    assert "bulls_join" not in verdicts(decision)


def test_bulls_last_night_win_does_not_block() -> None:
    decision = decide(invited(NIGHT, bulls_won_at=msk(1, 0)), only("bulls"), NIGHT)
    assert act(decision)[0] == "bulls_join"


def test_bulls_busy() -> None:
    busy = BusyState(activity="job", until=NIGHT + timedelta(minutes=1))
    decision = decide(invited(NIGHT, busy=busy), only("bulls"), NIGHT)
    assert verdicts(decision)["bulls_join"] == "busy"


# --- мандарин и смузи


def test_tangerine_first_send_and_interval() -> None:
    assert act(decide(state(NOON), only("tangerine"), NOON)) == ("tangerine", TANGERINE)
    last = {"tangerine": NOON - timedelta(hours=10)}
    decision = decide(state(NOON), only("tangerine"), NOON, last_done=last)
    assert isinstance(decision, Wait)
    # Запуск стартует раньше, чем /gt реально уходит: минута запаса к кулдауну игры.
    assert decision.until == NOON + timedelta(hours=10) + READY_SLACK + TIMER_MARGIN


def test_tangerine_refusals() -> None:
    cooldown = state(NOON, tangerine_ready_at=NOON + timedelta(hours=2))
    waiting = decide(cooldown, only("tangerine"), NOON)
    assert isinstance(waiting, Wait)
    assert waiting.until == NOON + timedelta(hours=2) + READY_SLACK + TIMER_MARGIN
    refused = state(NOON, tangerine_not_player=Obs(value="Настя", at=NOON - timedelta(hours=1)))
    decision = decide(refused, only("tangerine"), NOON)
    assert verdicts(decision)["tangerine"] == "not_player"
    old = state(NOON, tangerine_not_player=Obs(value="Настя", at=NOON - timedelta(hours=25)))
    assert act(decide(old, only("tangerine"), NOON))[0] == "tangerine"


def test_smoothie_today_recipe() -> None:
    fresh = state(NOON, smoothie_recipe=Obs(value=RECIPE, at=msk(10)))
    assert act(decide(fresh, only("smoothie"), NOON)) == ("smoothie", {"recipe": RECIPE.recipe})
    cooked = {"smoothie": msk(11)}
    assert "smoothie" not in verdicts(decide(fresh, only("smoothie"), NOON, last_done=cooked))
    yesterday = {"smoothie": msk(11, day=25)}
    decision = decide(fresh, only("smoothie"), NOON, last_done=yesterday)
    assert act(decision)[0] == "smoothie"


@pytest.mark.parametrize(
    "over",
    [
        {"smoothie_recipe": Obs(value=RECIPE, at=msk(2, 50))},
        {
            "smoothie_recipe": Obs(value=RECIPE, at=msk(10)),
            "smoothie_bonus": Obs(value="💡…", at=msk(10, 30)),
        },
        {
            "smoothie_recipe": Obs(value=RECIPE, at=msk(10)),
            "smoothie_ingredients": Obs(value={"carrot": 1}, at=msk(11)),
        },
    ],
)
def test_smoothie_skipped(over: dict[str, Any]) -> None:
    decision = decide(state(NOON, **over), only("smoothie"), NOON)
    assert "smoothie" not in verdicts(decision)


# --- окно сна


def test_sleep_waits_for_factory_signup_if_deadline_allows() -> None:
    now = msk(17, 55)
    early = state(now, sleep_deadline=msk(19), battle_at=msk(22))
    decision = decide(early, only("factory"), now)
    assert isinstance(decision, Wait) and decision.until == msk(18) + TIMER_MARGIN
    after = msk(18, 16)
    assert (
        act(
            decide(state(after, sleep_deadline=msk(19), battle_at=msk(22)), only("factory"), after)
        )[0]
        == "sleep"
    )
    tight = state(now, sleep_deadline=msk(18, 20), battle_at=msk(22))
    assert act(decide(tight, only("factory"), now))[0] == "sleep"


def test_sleep_waits_for_night_window() -> None:
    decision = decide(state(NOON), only(), NOON)
    assert isinstance(decision, Wait)
    assert decision.until == msk(22, 5) + TIMER_MARGIN


def test_sleep_after_bulls_when_invites_come() -> None:
    cfg = only("bulls", chats={"bulls_invite_chat_id": -100500})
    decision = decide(state(NOON), cfg, NOON)
    assert isinstance(decision, Wait)
    assert decision.until == msk(0, 30, day=27) + TIMER_MARGIN


def test_last_night_bulls_win_does_not_cancel_tonight() -> None:
    cfg = only("bulls", chats={"bulls_invite_chat_id": -100500})
    decision = decide(state(NOON, bulls_won_at=msk(1, 0)), cfg, NOON)
    assert isinstance(decision, Wait)
    assert decision.until == msk(0, 30, day=27) + TIMER_MARGIN


def test_sleep_in_night_window() -> None:
    now = msk(1, 0, day=27)
    assert act(decide(state(now), only(), now))[0] == "sleep"


def test_deadline_beats_night_window() -> None:
    soon = state(NOON, sleep_deadline=NOON + timedelta(hours=1))
    assert act(decide(soon, only(), NOON))[0] == "sleep"


def test_long_sleep_must_end_before_battle() -> None:
    cfg = only(sleep={"duration_h": 12})
    early = msk(0, 40, day=27)
    assert act(decide(state(early), cfg, early))[0] == "sleep"
    late = msk(1, 0, day=27)
    decision = decide(state(late), cfg, late)
    assert isinstance(decision, Wait)
    assert decision.until == msk(22, 5, day=27) + TIMER_MARGIN


# --- метро

METRO = only("metro")
# Только метро: без дел и сна решение — либо метро, либо ожидание.
METRO_ALONE = Settings.model_validate(
    {"features": {**dict.fromkeys(PHASE4, False), "metro": True, "deeds": False, "sleep": False}}
)
BATTLE_EVENING = msk(22)
DAY = timedelta(days=1)
METRO_PARAMS = {
    "battle_at": BATTLE_EVENING.isoformat(),
    "margin_min": 25,
    "buffs": ["fastMove", "strong", "firstAid"],
    "heal_at": 50,
    "heal_before_exit": True,
    "chest_min_packs": 2,
    "npc_low": True,
    "npc_high": False,
    "npc_min_stamina": 30,
}


def metro_state(now: datetime, **over: Any) -> CharacterState:
    fields: dict[str, Any] = {"motivation": 10, "battle_at": BATTLE_EVENING}
    return state(now, **{**fields, **over})


def test_metro_when_ready_and_battle_far() -> None:
    assert act(decide(metro_state(NOON), METRO, NOON)) == ("metro", METRO_PARAMS)
    ready = metro_state(NOON, metro_ready_at=Obs(value=NOON, at=NOON))
    assert act(decide(ready, METRO, NOON))[0] == "metro"
    assert act(decide(metro_state(NOON), only(), NOON))[0] != "metro"


def test_metro_budget_counts_from_start_of_battle_hour() -> None:
    # «Битва через 1 ч 24 мин» в 12:00 — это битва в 14:00 (отсчёт округлён вниз), а не 13:24.
    rounded_down = metro_state(NOON, battle_at=NOON + timedelta(minutes=84))
    assert act(decide(rounded_down, METRO, NOON)) == (
        "metro",
        {**METRO_PARAMS, "battle_at": msk(14).isoformat()},
    )


def test_metro_waits_for_cooldown() -> None:
    later = NOON + timedelta(hours=3)
    decision = decide(metro_state(NOON, metro_ready_at=later), METRO_ALONE, NOON)
    assert isinstance(decision, Wait)
    assert (decision.until, decision.reason) == (later + READY_SLACK + TIMER_MARGIN, "metro_ready")


def test_metro_cooldown_from_last_run_when_timer_unknown() -> None:
    last = NOON - timedelta(hours=10)
    decision = decide(metro_state(NOON), METRO_ALONE, NOON, last_done={"metro": last})
    assert isinstance(decision, Wait)
    assert decision.until == last + timedelta(hours=16) + TIMER_MARGIN
    old = NOON - timedelta(hours=17)
    assert act(decide(metro_state(NOON), METRO, NOON, last_done={"metro": old}))[0] == "metro"


@pytest.mark.parametrize(
    ("battle_in", "history", "chosen"),
    [
        (84, (), False),
        (86, (), True),
        (86, (3000.0,) * 10, False),
        (101, (3000.0,) * 10, True),
        (86, (1800.0,) * 9 + (6000.0,), True),
    ],
)
def test_metro_budget_before_battle(
    battle_in: int, history: tuple[float, ...], chosen: bool
) -> None:
    # Бюджет: max(60 мин, p90 × 1.5) + 15 + 10 мин запаса до битвы. Битва — в начале часа:
    # отсчёт «Битва через …» округлён вниз, поэтому её время нормируется, и сдвигается «сейчас».
    battle = msk(14)
    now = battle - timedelta(minutes=battle_in)
    s = metro_state(now, battle_at=battle)
    decision = decide(s, METRO, now, metro_durations=history)
    if chosen:
        assert act(decision)[0] == "metro"
    else:
        assert verdicts(decision)["metro"] == "battle_window"


def test_metro_needs_motivation_over_reserve() -> None:
    fight = GorbushkaState(
        state="waiting", won=1, total=4, next_fight_at=NOON + timedelta(minutes=20)
    )
    ok = metro_state(NOON, motivation=3, gorbushka=fight)
    assert act(decide(ok, METRO_ALONE, NOON))[0] == "metro"
    short = metro_state(NOON, motivation=2, gorbushka=fight)
    assert verdicts(decide(short, METRO_ALONE, NOON))["metro"] == "no_motivation"


def test_metro_needs_free_character_and_time_before_sleep() -> None:
    busy = BusyState(activity="job", until=NOON + timedelta(minutes=5))
    assert verdicts(decide(metro_state(NOON, busy=busy), METRO_ALONE, NOON))["metro"] == "busy"
    sleepy = metro_state(NOON, sleep_deadline=NOON + timedelta(minutes=30))
    assert verdicts(decide(sleepy, METRO_ALONE, NOON))["metro"] == "sleep_deadline"


def test_deeds_keep_motivation_for_metro_ready_soon() -> None:
    soon = metro_state(NOON, motivation=2, metro_ready_at=NOON + timedelta(minutes=30))
    decision = decide(soon, METRO, NOON)
    assert isinstance(decision, Wait)
    assert {v for k, v in verdicts(decision).items() if k.startswith("deed:")} == {"no_motivation"}
    assert act(decide(soon, only(), NOON))[0].startswith("deed:")


def test_metro_resumed_after_restart_regardless_of_cooldown() -> None:
    inside = Obs(value=3624441, at=NOON - timedelta(minutes=3))
    s = metro_state(
        NOON, metro_message=inside, metro_ready_at=NOON + timedelta(hours=16), motivation=0
    )
    decision = decide(s, METRO_ALONE, NOON, last_done={"metro": NOON - timedelta(minutes=20)})
    assert act(decision) == ("metro", {**METRO_PARAMS, "resume": 3624441})


@pytest.mark.parametrize(
    ("seen_min_ago", "battle", "flags"),
    [
        (150, BATTLE_EVENING, {}),
        # Последний кадр — до выброса за 15 минут до битвы, а битва уже прошла.
        (30, NOON - timedelta(minutes=10), {}),
        (3, BATTLE_EVENING, {"metro": False}),
    ],
)
def test_metro_not_resumed(seen_min_ago: int, battle: datetime, flags: dict[str, bool]) -> None:
    inside = Obs(value=3624441, at=NOON - timedelta(minutes=seen_min_ago))
    s = metro_state(NOON, metro_message=inside, battle_at=battle, metro_ready_at=NOON + DAY)
    cfg = METRO_ALONE.model_copy(
        update={"features": METRO_ALONE.features.model_copy(update=flags)}
    )
    decision = decide(s, cfg, NOON)
    assert not isinstance(decision, Act) or "resume" not in decision.params


def test_metro_resume_counts_kick_from_start_of_battle_hour() -> None:
    # Профиль в 12:00: «Битва через 1 ч 58 мин» — битва в 14:00, игра выкинет в 13:45.
    battle = Obs(value=NOON + timedelta(minutes=118, seconds=30), at=NOON)
    now = msk(13, 44)
    inside = Obs(value=3624441, at=now - timedelta(minutes=2))
    s = metro_state(now, metro_message=inside, battle_at=battle, metro_ready_at=now + DAY)
    decision = decide(s, METRO_ALONE, now)
    assert act(decision) == (
        "metro",
        {**METRO_PARAMS, "battle_at": msk(14).isoformat(), "resume": 3624441},
    )
    later = msk(13, 46)
    gone = metro_state(later, metro_message=inside, battle_at=battle, metro_ready_at=later + DAY)
    assert isinstance(decide(gone, METRO_ALONE, later), Wait)


def test_unknown_last_run_screen_blocks_resume() -> None:
    inside = Obs(value=3624441, at=NOON - timedelta(minutes=3), src="doubtful")
    s = metro_state(NOON, metro_message=inside, metro_ready_at=NOON + DAY)
    decision = decide(s, METRO_ALONE, NOON)
    assert isinstance(decision, Wait)
    assert verdicts(decision)["metro"] == "metro_unknown_screen"


def test_left_metro_is_not_resumed() -> None:
    left = metro_state(NOON, metro_message=None, metro_ready_at=NOON + DAY)
    assert isinstance(decide(left, METRO_ALONE, NOON), Wait)
