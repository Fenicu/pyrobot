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

PHASE4 = ("stocks_dump", "factory", "bulls", "tangerine", "smoothie")
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
        "battle_at": at(9 * 60),
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
