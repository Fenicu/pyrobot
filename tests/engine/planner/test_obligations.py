from datetime import datetime, timedelta
from typing import Any

import pytest

from app.engine.gametime import MSK
from app.engine.planner.base import READY_SLACK, TIMER_MARGIN, battle_hour
from app.engine.planner.decide import decide, outlook
from app.engine.planner.types import Act, Decision, Wait
from app.engine.scenarios.registry import CERTIFIED
from app.engine.settings import Settings
from app.engine.state.model import (
    BusyState,
    CharacterState,
    FoodStockState,
    GorbushkaState,
    MetroRunRef,
    Obs,
    SmoothieRecipeState,
    StockLimits,
    TargetSet,
)
from tests.engine.test_settings import limited_settings

PHASE4 = (
    "stocks_dump",
    "factory",
    "bulls",
    "tangerine",
    "tangerine_gifts",
    "smoothie",
    "metro",
    "lottery",
    "trips",
)
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
    return Settings.model_validate({**sections, "features": {**flags, "daily_tasks": False}})


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
        "battle_target": "🛡Защита",
        "company": "bmesa",
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
    assert act(decision) == ("battle_target", {"target": "🛡Защита"})


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


def test_defense_shown_in_profile_is_ready() -> None:
    battle = msk(13)
    cfg = only(battle={"target": "🛡Защита"})
    shown = state(NOON, battle_at=battle, battle_target="🛡Защита")
    assert "battle_target" not in verdicts(decide(shown, cfg, NOON))
    attack = state(NOON, battle_at=battle, battle_target="🤖Hooli")
    assert act(decide(attack, cfg, NOON)) == ("battle_target", {"target": "🛡Защита"})


def test_profile_with_other_target_is_reset() -> None:
    decision = decide(state(NOON, battle_target="🤖Hooli"), only(), NOON)
    assert act(decision) == ("battle_target", {"target": "🛡Защита"})


def test_target_old_profile_does_not_count() -> None:
    before = Obs(value="🛡Защита", at=NOON - timedelta(hours=10))
    decision = decide(state(NOON, battle_target=before), only(), NOON)
    assert act(decision)[0] == "battle_target"


def test_target_set_while_sleeping() -> None:
    sleeping = BusyState(activity="sleep_hotel", until=NOON + timedelta(hours=3))
    decision = decide(state(NOON, busy=sleeping, battle_target=None), only(), NOON)
    assert act(decision) == ("battle_target", {"target": "🛡Защита"})


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


def fed(**sections: Any) -> Settings:
    """`only()` с включённым фастфудом: по умолчанию он выключен."""
    cfg = only(**sections)
    return cfg.model_copy(update={"features": cfg.features.model_copy(update={"fastfood": True})})


def test_zero_stamina_before_battle_eats_when_no_fastfood() -> None:
    now = msk(12, 40)
    hungry = state(now, stamina=0, battle_at=msk(13))
    # Фастфуд по умолчанию выключен: 🔋 к битве восстанавливает обычная еда.
    assert act(decide(hungry, only(), now)) == ("deed:eat", {})
    with_food = state(now, stamina=0, battle_at=msk(13))
    decision = decide(with_food, fed(), now)
    assert act(decision) == ("fastfood", {"food": "hotdog"})
    assert isinstance(decision, Act) and decision.reason == "battle_stamina"


def test_battle_stamina_uses_fastfood_rules() -> None:
    now, battle = msk(12, 40), msk(13)
    bananas = {"banana": FoodStockState(count=10, low=150, high=275)}
    only_bananas = state(now, stamina=0, battle_at=battle, food_stock=bananas)
    assert act(decide(only_bananas, fed(), now)) == ("deed:eat", {})
    late = state(now, stamina=0, battle_at=battle, fastfood_ready_at=battle)
    assert act(decide(late, fed(), now)) == ("deed:eat", {})
    soon = state(now, stamina=0, battle_at=battle, fastfood_ready_at=now + timedelta(minutes=5))
    decision = decide(soon, fed(), now)
    assert isinstance(decision, Wait) and decision.reason == "fastfood_ready"


def test_battle_stamina_refreshes_stale_food_before_paid_eat() -> None:
    now = msk(12, 40)
    old_food = Obs(value=FOOD, at=now - timedelta(hours=7))
    hungry = state(now, stamina=0, battle_at=msk(13), food_stock=old_food)
    decision = decide(hungry, fed(), now)
    assert act(decision) == ("refresh", {"source": "food"})
    assert verdicts(decision)["battle_stamina"] == "stale:food_stock"


def test_battle_stamina_without_fastfood_eats_below_full() -> None:
    # Фастфуд выключен: персонаж ест сам 🍴 перед битвой, как только 🔋 не полная.
    now = msk(12, 40)
    tired = state(now, stamina=60, battle_at=msk(13))
    decision = decide(tired, only(), now)
    assert act(decision) == ("deed:eat", {})
    assert isinstance(decision, Act) and decision.reason == "battle_stamina"
    assert "deed:eat" not in verdicts(decide(state(now, battle_at=msk(13)), only(), now))
    # С фастфудом — как раньше: только на нуле.
    assert "deed:eat" not in verdicts(decide(tired, fed(), now))


def test_battle_stamina_eat_keeps_money_reserves() -> None:
    now = msk(12, 40)
    ticket = GorbushkaState(state="need_ticket")
    # 💵 124: на еду (5) хватает, но не сверх билета Горбушки (120).
    poor = state(now, stamina=60, battle_at=msk(13), money=124, gorbushka=ticket)
    decision = decide(poor, only(), now)
    assert verdicts(decision)["deed:eat"] == "no_money"
    rich = state(now, stamina=60, battle_at=msk(13), money=125, gorbushka=ticket)
    assert act(decide(rich, only(), now)) == ("deed:eat", {})


def test_battle_stamina_eat_waits_while_busy() -> None:
    now = msk(12, 40)
    busy = BusyState(activity="harvest", until=now + timedelta(minutes=3))
    decision = decide(state(now, stamina=60, battle_at=msk(13), busy=busy), only(), now)
    assert "deed:eat" not in verdicts(decision)


def test_battle_stamina_eat_only_if_done_before_battle() -> None:
    no_fastfood = Settings.model_validate(
        {"features": {**{name: False for name in PHASE4}, "fastfood": False}}
    )
    late = msk(12, 50)
    decision = decide(state(late, stamina=0, battle_at=msk(13)), no_fastfood, late)
    assert verdicts(decision)["deed:eat"] == "battle_window"
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


def test_dump_skips_own_company() -> None:
    # Своя компания — из профиля: её акции бот сам не покупает, какой бы она ни была.
    now = msk(12, 50)
    decision = decide(dumping(now, company="stark"), only("stocks_dump"), now)
    assert verdicts(decision)["stocks_dump"] == "no_stock"
    decision = decide(dumping(now, company="umbrl"), only("stocks_dump"), now)
    assert act(decision) == ("stocks_dump", {"keep": 150 + 210, "margin": 5})


def test_dump_needs_own_company() -> None:
    # Своя компания неизвестна — сначала профиль, а не покупка наугад.
    now = msk(12, 50)
    unknown = dumping(now).model_copy(update={"company": None})
    decision = decide(unknown, only("stocks_dump"), now)
    assert act(decision) == ("refresh", {"source": "profile"})
    assert verdicts(decision)["stocks_dump"] == "stale:company"


def test_dump_skips_unrecognized_own_company() -> None:
    # Значок в свежем профиле не распознан: своя неизвестна, а профиль заново её не покажет.
    now = msk(12, 50)
    decision = decide(dumping(now, company=None), only("stocks_dump"), now)
    assert "refresh" not in verdicts(decision)
    assert verdicts(decision)["stocks_dump"] == "company_unknown"


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


def test_hotel_reserve_with_certified_sleep_in_live() -> None:
    # Сон сертифицирован: в live он исполнится, поэтому слив держит деньги на отель.
    now = msk(12, 50)
    rich_hotel = only("stocks_dump", sleep={"hotel_if_cash_after_reserve_ge": 500})
    decision = decide(dumping(now), rich_hotel, now, certified=CERTIFIED)
    assert act(decision) == ("stocks_dump", {"keep": 150 + 500, "margin": 5})


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


def test_teamless_character_skips_factory() -> None:
    # Битва за фабрику — только для команд: вне команды ни записи, ни окна для дел, ни отчёта.
    now = msk(18, 5)
    decision = decide(state(now, team_tag=None), only("factory"), now)
    assert verdicts(decision)["factory_signup"] == "no_team"
    assert not isinstance(decision, Act)
    early = msk(17, 57)
    cfg = only("factory", strategy={"deeds": ["harvest", "job"]})
    decision = decide(state(early, motivation=40, team_tag=None), cfg, early)
    assert isinstance(decision, Act) and "factory_window" not in verdicts(decision).values()
    later = msk(18, 40)
    decision = decide(state(later, team_tag=None), only("factory"), later, last_done=SIGNED)
    assert "factory_report" not in verdicts(decision)
    view = outlook(state(later, team_tag=None), only("factory"), later, last_done=SIGNED)
    assert all(w.kind != "factory_report" for w in view.wakeups)
    # В команде — как раньше.
    assert act(decide(state(now, team_tag="SU"), only("factory"), now)) == ("factory_signup", {})


def test_old_teamless_profile_is_refreshed_before_factory() -> None:
    # «Без команды» — из профиля получасовой давности: игрок мог вступить в команду. У окна записи
    # сначала профиль, а не отказ no_team; окно для дел и сна — как в команде, пока не ясно.
    now = msk(18, 5)
    old = Obs(value=None, at=msk(17, 35))
    decision = decide(state(now, team_tag=old), only("factory"), now)
    assert act(decision) == ("refresh", {"source": "profile"})
    assert verdicts(decision)["factory_signup"] == "stale:team_tag"
    # Лимит обновлений не дал — ждём его, без записи и без no_team.
    limited = decide(state(now, team_tag=old), only("factory"), now, last_refresh={"profile": now})
    assert not isinstance(limited, Act)
    assert verdicts(limited)["factory_signup"] == "stale:team_tag"
    early = msk(17, 57)
    cfg = only("factory", strategy={"deeds": ["harvest", "job"]})
    decision = decide(state(early, motivation=40, team_tag=old), cfg, early)
    assert verdicts(decision)["deed:harvest"] == "factory_window"
    # Свежий профиль: вступил — запись; всё ещё без команды — no_team.
    joined = state(now, team_tag=Obs(value="SU", at=now))
    assert act(decide(joined, only("factory"), now)) == ("factory_signup", {})
    still = decide(state(now, team_tag=Obs(value=None, at=now)), only("factory"), now)
    assert verdicts(still)["factory_signup"] == "no_team"


def test_teamless_sleep_does_not_wait_for_factory() -> None:
    now = msk(17, 55)
    early = state(now, sleep_deadline=msk(19), battle_at=msk(22), team_tag=None)
    assert act(decide(early, only("factory"), now))[0] == "sleep"


def test_factory_waits_for_busy_then_after_close_forgets() -> None:
    now = msk(18, 5)
    busy = BusyState(activity="job", until=now + timedelta(minutes=1))
    decision = decide(state(now, busy=busy), only("factory"), now)
    assert verdicts(decision)["factory_signup"] == "busy"
    late = msk(18, 20)
    assert "factory_signup" not in verdicts(decide(state(late), only("factory"), late))


SIGNED = {"factory_signup": msk(18, 1)}


def test_factory_report_after_battle_if_signed_today() -> None:
    now = msk(18, 40)
    decision = decide(state(now), only("factory"), now, last_done=SIGNED)
    assert act(decision) == ("factory_report", {})


def test_factory_report_by_signed_screen() -> None:
    now = msk(18, 40)
    signed = {"factory_signed": Obs(value=True, at=msk(18, 3))}
    assert act(decide(state(now, **signed), only("factory"), now))[0] == "factory_report"


def test_factory_report_waits_for_battle_end() -> None:
    now = msk(18, 20)
    decision = decide(state(now), only("factory"), now, last_done=SIGNED)
    assert isinstance(decision, Wait)
    assert decision.reason == "factory_report" and decision.until == msk(18, 31) + TIMER_MARGIN


@pytest.mark.parametrize(
    ("now", "last_done", "over"),
    [
        # Не записан сегодня: запись вчера, пропуск после победы.
        (msk(18, 40), {"factory_signup": msk(18, 1, day=25)}, {}),
        (msk(18, 40), {}, {"factory_skip": Obs(value=True, at=msk(18, 1))}),
        # Сегодняшний отчёт уже получен (ручной /fb тоже).
        (msk(18, 40), SIGNED, {"factory_report_day": Obs(value=msk(18).date(), at=msk(18, 35))}),
        # Один раз в день: сегодняшний запуск уже был.
        (msk(19, 40), {**SIGNED, "factory_report": msk(18, 32)}, {}),
        # После 23:59 не пытаться.
        (msk(23, 59), SIGNED, {}),
    ],
)
def test_factory_report_not_needed(
    now: datetime, last_done: dict[str, datetime], over: dict[str, Any]
) -> None:
    decision = decide(state(now, **over), only("factory"), now, last_done=last_done)
    assert "factory_report" not in verdicts(decision)
    assert all(
        w.kind != "factory_report"
        for w in outlook(state(now, **over), only("factory"), now, last_done=last_done).wakeups
    )


def test_factory_report_off_with_factory_feature() -> None:
    now = msk(18, 40)
    assert "factory_report" not in verdicts(decide(state(now), only(), now, last_done=SIGNED))


def test_factory_report_not_in_sleep() -> None:
    now = msk(18, 40)
    asleep = BusyState(activity="sleep_bridge", until=now + timedelta(hours=5))
    decision = decide(state(now, busy=asleep), only("factory"), now, last_done=SIGNED)
    assert isinstance(decision, Wait) and "factory_report" not in verdicts(decision)


def test_factory_report_while_busy_with_deed() -> None:
    # /fb — навигация: во время дела игра его принимает.
    now = msk(18, 40)
    busy = BusyState(activity="job", until=now + timedelta(minutes=5))
    decision = decide(state(now, busy=busy), only("factory"), now, last_done=SIGNED)
    assert act(decision) == ("factory_report", {})


def test_factory_report_uncertified_in_live() -> None:
    now = msk(18, 40)
    decision = decide(state(now), only("factory"), now, last_done=SIGNED, certified=frozenset())
    assert verdicts(decision)["factory_report"] == "uncertified"
    assert "factory_report" in CERTIFIED


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


TANGERINE_ON = only("tangerine", chats={"tangerine_reply_to": 927136})


def test_tangerine_needs_recipient() -> None:
    # Адресат мандаринов — выбор игрока: по умолчанию не задан, и /gt не уходит.
    decision = decide(state(NOON), only("tangerine"), NOON)
    assert "tangerine" not in verdicts(decision)
    assert act(decide(state(NOON), TANGERINE_ON, NOON)) == ("tangerine", TANGERINE)


def test_tangerine_first_send_and_interval() -> None:
    assert act(decide(state(NOON), TANGERINE_ON, NOON)) == ("tangerine", TANGERINE)
    last = {"tangerine": NOON - timedelta(hours=10)}
    decision = decide(state(NOON), TANGERINE_ON, NOON, last_done=last)
    assert isinstance(decision, Wait)
    # Запуск стартует раньше, чем /gt реально уходит: минута запаса к кулдауну игры.
    assert decision.until == NOON + timedelta(hours=10) + READY_SLACK + TIMER_MARGIN


def test_tangerine_refusals() -> None:
    cooldown = state(NOON, tangerine_ready_at=NOON + timedelta(hours=2))
    waiting = decide(cooldown, TANGERINE_ON, NOON)
    assert isinstance(waiting, Wait)
    assert waiting.until == NOON + timedelta(hours=2) + READY_SLACK + TIMER_MARGIN
    refused = state(NOON, tangerine_not_player=Obs(value="Настя", at=NOON - timedelta(hours=1)))
    decision = decide(refused, TANGERINE_ON, NOON)
    assert verdicts(decision)["tangerine"] == "not_player"
    old = state(NOON, tangerine_not_player=Obs(value="Настя", at=NOON - timedelta(hours=25)))
    assert act(decide(old, TANGERINE_ON, NOON))[0] == "tangerine"


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


# Дедлайн сна утром 27-го: последняя ночь перед ним — сегодняшняя.
TONIGHT_LAST = msk(10, day=27)


def test_sleep_waits_for_night_window() -> None:
    decision = decide(state(NOON, sleep_deadline=TONIGHT_LAST), only(), NOON)
    assert isinstance(decision, Wait)
    assert decision.until == msk(22, 5) + TIMER_MARGIN


def test_sleep_after_bulls_when_invites_come() -> None:
    cfg = only("bulls", chats={"bulls_invite_chat_id": -100500})
    decision = decide(state(NOON, sleep_deadline=TONIGHT_LAST), cfg, NOON)
    assert isinstance(decision, Wait)
    assert decision.until == msk(0, 30, day=27) + TIMER_MARGIN


def test_last_night_bulls_win_does_not_cancel_tonight() -> None:
    cfg = only("bulls", chats={"bulls_invite_chat_id": -100500})
    decision = decide(state(NOON, bulls_won_at=msk(1, 0), sleep_deadline=TONIGHT_LAST), cfg, NOON)
    assert isinstance(decision, Wait)
    assert decision.until == msk(0, 30, day=27) + TIMER_MARGIN


def test_sleep_in_night_window() -> None:
    now = msk(1, 0, day=27)
    decision = decide(state(now, sleep_deadline=TONIGHT_LAST), only(), now)
    assert act(decision)[0] == "sleep" and decision.reason == "sleep_night"


def test_deadline_beats_night_window() -> None:
    soon = state(NOON, sleep_deadline=NOON + timedelta(hours=1))
    decision = decide(soon, only(), NOON)
    assert act(decision)[0] == "sleep" and decision.reason == "sleep_deadline"


# Подъём в 05:05 26-го: лечь снова игра разрешит через 12 часов, дедлайн — через 72 часа.
WOKE = msk(5, 5)
BULLS = only("bulls", chats={"bulls_invite_chat_id": -100500})


def rested(now: datetime, awake_h: int = 72) -> CharacterState:
    return state(
        now,
        sleep_deadline=WOKE + timedelta(hours=awake_h),
        sleep_allowed_at=WOKE + timedelta(hours=12),
    )


def sleep_window(s: CharacterState, cfg: Settings, now: datetime) -> datetime:
    view = outlook(s, cfg, now)
    return next(w.at for w in view.wakeups if w.kind == "sleep_window") - TIMER_MARGIN


def test_sleep_in_last_night_before_deadline() -> None:
    assert sleep_window(rested(NOON), only(), NOON) == msk(22, 5, day=28)
    assert sleep_window(rested(NOON), BULLS, NOON) == msk(0, 30, day=29)


def test_last_night_skips_bulls_when_only_evening_fits() -> None:
    # Дедлайн 29-го в 01:00: лечь не позже 23:00 28-го — 00:30 не успевает, 22:05 того же вечера
    # успевает: эта ночь без биржевиков, а не сон на сутки раньше.
    tight = state(NOON, sleep_deadline=msk(1, day=29), sleep_allowed_at=WOKE + timedelta(hours=12))
    assert sleep_window(tight, BULLS, NOON) == msk(22, 5, day=28)
    night = msk(22, 5, day=28)
    decision = decide(state(night, sleep_deadline=msk(1, day=29)), BULLS, night)
    assert act(decision)[0] == "sleep" and decision.reason == "sleep_night"


def test_last_night_not_before_sleep_allowed() -> None:
    # Разрешение лечь посреди последней ночи — сон с разрешения.
    mid = state(NOON, sleep_deadline=msk(5, 5, day=29), sleep_allowed_at=msk(1, day=29))
    assert sleep_window(mid, only(), NOON) == msk(1, day=29)
    # Разрешение после последней ночи — сон за lead_min до дедлайна.
    late = state(NOON, sleep_deadline=msk(17, day=29), sleep_allowed_at=msk(6, day=29))
    assert sleep_window(late, only(), NOON) == msk(15, day=29)
    then = msk(15, day=29)
    decision = decide(
        state(then, sleep_deadline=msk(17, day=29), sleep_allowed_at=msk(6, day=29)), only(), then
    )
    assert act(decision)[0] == "sleep" and decision.reason == "sleep_deadline"


def test_no_sleep_in_earlier_nights() -> None:
    tonight = msk(22, 5)
    assert "sleep" not in verdicts(decide(rested(tonight), only(), tonight))
    assert sleep_window(rested(tonight), only(), tonight) == msk(22, 5, day=28)
    midnight = msk(1, 0, day=28)
    assert sleep_window(rested(midnight), only(), midnight) == msk(22, 5, day=28)


def test_last_night_sleep_has_night_reason() -> None:
    night = msk(22, 5, day=28)
    decision = decide(rested(night), only(), night)
    assert act(decision)[0] == "sleep" and decision.reason == "sleep_night"


def test_deadline_before_any_night_sleeps_at_deadline_minus_lead() -> None:
    near = state(NOON, sleep_deadline=msk(23), sleep_allowed_at=WOKE + timedelta(hours=12))
    assert sleep_window(near, only(), NOON) == msk(21)
    late = msk(21)
    decision = decide(state(late, sleep_deadline=msk(23)), only(), late)
    assert act(decision)[0] == "sleep" and decision.reason == "sleep_deadline"


def test_longer_deadline_moves_sleep_to_later_night() -> None:
    assert sleep_window(rested(NOON, awake_h=120), only(), NOON) == msk(22, 5, day=30)


def test_long_sleep_must_end_before_battle() -> None:
    cfg = only(sleep={"duration_h": 12})
    early = msk(0, 40, day=27)
    assert act(decide(state(early, sleep_deadline=TONIGHT_LAST), cfg, early))[0] == "sleep"
    late = msk(1, 0, day=27)
    decision = decide(state(late, sleep_deadline=msk(10, day=28)), cfg, late)
    assert isinstance(decision, Wait)
    assert decision.until == msk(22, 5, day=27) + TIMER_MARGIN


# --- метро

METRO = only("metro")
# Только метро: без дел и сна решение — либо метро, либо ожидание.
METRO_ALONE = Settings.model_validate(
    {
        "features": {
            **dict.fromkeys(PHASE4, False),
            "metro": True,
            "deeds": False,
            "sleep": False,
            "daily_tasks": False,
        }
    }
)
BATTLE_EVENING = msk(22)
DAY = timedelta(days=1)
METRO_PARAMS = {
    "battle_at": BATTLE_EVENING.isoformat(),
    "margin_min": 25,
    "buffs": ["firstAid", "strong", "fastMove"],
    "heal_at": 50,
    "heal_before_exit": True,
    "chest_min_packs": 1,
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


def test_metro_without_fastfood_eats_before_entry() -> None:
    # Фастфуд выключен: перед спуском персонаж ест сам 🍴 до 100% 🔋, потом — метро.
    tired = metro_state(NOON, stamina=70)
    decision = decide(tired, METRO, NOON)
    assert act(decision) == ("deed:eat", {})
    assert isinstance(decision, Act) and decision.reason == "metro_stamina"
    assert act(decide(metro_state(NOON), METRO, NOON)) == ("metro", METRO_PARAMS)
    # С фастфудом — сразу в метро, как раньше.
    with_food = METRO.model_copy(
        update={"features": METRO.features.model_copy(update={"fastfood": True})}
    )
    assert act(decide(tired, with_food, NOON)) == ("metro", METRO_PARAMS)


def test_metro_entry_eat_follows_deed_rules() -> None:
    tired = metro_state(NOON, stamina=70)
    # 💵 на еду нет или дела выключены — метро без еды.
    poor = metro_state(NOON, stamina=70, money=4)
    decision = decide(poor, METRO, NOON)
    assert act(decision) == ("metro", METRO_PARAMS)
    assert verdicts(decision)["deed:eat"] == "no_money"
    no_deeds = METRO.model_copy(
        update={"features": METRO.features.model_copy(update={"deeds": False})}
    )
    assert act(decide(tired, no_deeds, NOON)) == ("metro", METRO_PARAMS)
    # Кулдаун сценария еды (отказ или сбой) — тоже без еды, а не по кругу.
    cooled = decide(tired, METRO, NOON, cooldowns={"deed:eat": NOON + timedelta(minutes=5)})
    assert act(cooled) == ("metro", METRO_PARAMS)


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
    assert verdicts(decide(short, METRO_ALONE, NOON))["metro"] == "reserved"
    empty = metro_state(NOON, motivation=1, gorbushka=fight)
    assert verdicts(decide(empty, METRO_ALONE, NOON))["metro"] == "no_motivation"
    # Спуск и без запаса не прошёл бы (не сертифицирован): причина — не запас.
    closed = decide(short, METRO_ALONE, NOON, certified=frozenset({"refresh"}))
    assert verdicts(closed)["metro"] == "no_motivation"


def test_metro_needs_free_character_and_time_before_sleep() -> None:
    busy = BusyState(activity="job", until=NOON + timedelta(minutes=5))
    assert verdicts(decide(metro_state(NOON, busy=busy), METRO_ALONE, NOON))["metro"] == "busy"
    sleepy = metro_state(NOON, sleep_deadline=NOON + timedelta(minutes=30))
    assert verdicts(decide(sleepy, METRO_ALONE, NOON))["metro"] == "sleep_deadline"


def test_deeds_keep_motivation_for_metro_ready_soon() -> None:
    soon = metro_state(NOON, motivation=2, metro_ready_at=NOON + timedelta(minutes=30))
    decision = decide(soon, METRO, NOON)
    assert isinstance(decision, Wait)
    assert {v for k, v in verdicts(decision).items() if k.startswith("deed:")} == {"reserved"}
    assert act(decide(soon, only(), NOON))[0].startswith("deed:")


@pytest.mark.parametrize(
    ("ahead", "ready_in", "reserved"),
    [(None, 59, True), (None, 61, False), (0, 30, False), (120, 119, True), (120, 121, False)],
)
def test_metro_reserve_horizon_from_settings(
    ahead: int | None, ready_in: int, reserved: bool
) -> None:
    strategy = {} if ahead is None else {"reserve_ahead_min": {"metro": ahead}}
    soon = metro_state(NOON, motivation=2, metro_ready_at=NOON + timedelta(minutes=ready_in))
    decision = decide(soon, only("metro", strategy=strategy), NOON)
    if reserved:
        assert isinstance(decision, Wait)
    else:
        assert act(decision)[0].startswith("deed:")


def test_zero_metro_horizon_keeps_nothing_even_when_metro_is_open() -> None:
    # Метро открыто, но спуск запрещён (не сертифицирован): дела берут 🔥 только без запаса.
    certified = frozenset({"deed:job", "refresh"})
    cfg = only("metro", strategy={"reserve_ahead_min": {"metro": 0}, "deeds": ["job"]})
    assert act(decide(metro_state(NOON, motivation=2), cfg, NOON, certified=certified)) == (
        "deed:job",
        {},
    )
    held = only("metro", strategy={"deeds": ["job"]})
    decision = decide(metro_state(NOON, motivation=2), held, NOON, certified=certified)
    assert isinstance(decision, Wait)


def test_planner_runs_on_limit_durations() -> None:
    # Все длительности на верхнем пределе: проход и решение считаются без переполнения timedelta.
    cfg = limited_settings()
    states = [
        metro_state(NOON),
        state(NOON, sleep_deadline=NOON + timedelta(hours=3), motivation=5),
        state(msk(21, 50), battle_at=msk(22), money=5000),
    ]
    for s in states:
        for last in ({}, {"tangerine": NOON - timedelta(hours=30), "metro": NOON - DAY}):
            decision = decide(s, cfg, NOON, last_done=last)
            view = outlook(s, cfg, NOON, last_done=last)
            assert view.decision == decision
    # Бюджет забега — сутки: до битвы в 22:00 спуск не помещается.
    alone = limited_settings().model_copy(update={"features": METRO_ALONE.features})
    assert verdicts(decide(metro_state(NOON), alone, NOON))["metro"] == "battle_window"


def run_in(
    seen: datetime, battle: Obs[datetime] | None = None, src: str = "screen"
) -> Obs[MetroRunRef]:
    """Идущий забег: последний экран в `seen`, битва — наблюдение на момент входа."""
    known = battle or Obs(value=BATTLE_EVENING, at=seen)
    return Obs(value=MetroRunRef(message_id=3624441, battle_at=known), at=seen, src=src)


def candidates(decision: Decision) -> list[tuple[str, str]]:
    return [(c.scenario, c.verdict) for c in decision.candidates]


def test_metro_resumed_after_restart_regardless_of_cooldown() -> None:
    inside = run_in(NOON - timedelta(minutes=3))
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
    seen = NOON - timedelta(minutes=seen_min_ago)
    inside = run_in(seen, Obs(value=battle, at=seen))
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
    inside = run_in(now - timedelta(minutes=2), battle)
    s = metro_state(now, metro_message=inside, battle_at=battle, metro_ready_at=now + DAY)
    decision = decide(s, METRO_ALONE, now)
    assert act(decision) == (
        "metro",
        {**METRO_PARAMS, "battle_at": msk(14).isoformat(), "resume": 3624441},
    )
    later = msk(13, 46)
    gone = metro_state(later, metro_message=inside, battle_at=battle, metro_ready_at=later + DAY)
    assert isinstance(decide(gone, METRO_ALONE, later), Wait)


def test_resume_counts_kick_from_battle_of_its_own_run() -> None:
    # Пауза в 12:40 (битва 13:00, игра выкинула в 12:45); в 13:10 профиль уже показывает
    # следующую битву — забег, из которого игра выкинула, не продолжается.
    entered = Obs(value=msk(13), at=msk(12, 20))
    inside = run_in(msk(12, 40), entered)
    ready = Obs(value=msk(12, 20), at=msk(12, 20))
    now = msk(13, 10)
    s = metro_state(
        now,
        metro_message=inside,
        battle_at=Obs(value=msk(16), at=msk(13, 5)),
        metro_ready_at=ready,
    )
    decision = decide(s, METRO_ALONE, now, last_done={"metro": now - timedelta(hours=20)})
    assert not isinstance(decision, Act) or "resume" not in decision.params
    # До выброса параметры продолжения — битва забега, а не последняя увиденная.
    early = msk(12, 44)
    s = metro_state(
        early,
        metro_message=inside,
        battle_at=Obs(value=msk(16), at=msk(12, 43)),
        metro_ready_at=ready,
    )
    assert act(decide(s, METRO_ALONE, early)) == (
        "metro",
        {**METRO_PARAMS, "battle_at": msk(13).isoformat(), "resume": 3624441},
    )


def test_no_new_entrance_while_inside_until_kick() -> None:
    # Забег начат в 09:00, последний экран 09:40, процесс лежал до 12:00: продолжать поздно,
    # но персонаж в метро до выброса (21:45) — нового входа нет, ожидание выброса.
    entered = NOON - timedelta(hours=3)
    ready = Obs(value=entered, at=entered)
    stale = run_in(NOON - timedelta(minutes=140))
    s = metro_state(NOON, metro_message=stale, metro_ready_at=ready)
    decision = decide(s, METRO_ALONE, NOON, last_done={"metro": NOON - timedelta(hours=20)})
    assert isinstance(decision, Wait)
    assert (decision.until, decision.reason) == (msk(21, 45) + TIMER_MARGIN, "metro_kick")
    assert ("metro", "in_metro") in candidates(decision)
    doubtful = run_in(NOON - timedelta(minutes=3), src="doubtful")
    s = metro_state(NOON, metro_message=doubtful, metro_ready_at=ready)
    decision = decide(s, METRO_ALONE, NOON, last_done={"metro": NOON - timedelta(hours=20)})
    assert isinstance(decision, Wait)
    assert ("metro", "metro_unknown_screen") in candidates(decision)
    assert ("metro", "in_metro") in candidates(decision)
    after = msk(21, 50)
    s = metro_state(after, metro_message=stale, metro_ready_at=ready)
    assert ("metro", "in_metro") not in candidates(decide(s, METRO_ALONE, after))


def test_unknown_last_run_screen_blocks_resume() -> None:
    inside = run_in(NOON - timedelta(minutes=3), src="doubtful")
    s = metro_state(NOON, metro_message=inside, metro_ready_at=NOON + DAY)
    decision = decide(s, METRO_ALONE, NOON)
    assert isinstance(decision, Wait)
    assert ("metro", "metro_unknown_screen") in candidates(decision)


def test_left_metro_is_not_resumed() -> None:
    left = metro_state(NOON, metro_message=None, metro_ready_at=NOON + DAY)
    assert isinstance(decide(left, METRO_ALONE, NOON), Wait)


# --- метро: итог без подтверждённого выхода

EXIT_AT = NOON - timedelta(minutes=5)
PROBE_KEY = "metro_probe"


def exited(at: datetime = EXIT_AT, battle: datetime = BATTLE_EVENING) -> Obs[MetroRunRef]:
    """Итог забега в `at`, выход ещё не подтвердил ответ игры."""
    known = Obs(value=battle, at=at - timedelta(hours=1))
    run = MetroRunRef(message_id=3624441, battle_at=known, exit_at=at, exit_loot={"money": 156})
    return Obs(value=run, at=at)


def stuck_state(now: datetime, **over: Any) -> CharacterState:
    fields: dict[str, Any] = {
        "metro_message": exited(),
        "metro_ready_at": Obs(value=EXIT_AT + timedelta(hours=16), at=EXIT_AT, src="derived"),
        # Профиль старый: без отметки забега планировщик его обновил бы.
        "busy": Obs(value=None, at=now - timedelta(hours=3)),
    }
    return metro_state(now, **{**fields, **over})


def test_unconfirmed_exit_holds_every_send_and_resume() -> None:
    decision = decide(stuck_state(NOON), METRO_ALONE, NOON)
    assert isinstance(decision, Wait)
    assert (decision.until, decision.reason) == (
        EXIT_AT + timedelta(minutes=30) + TIMER_MARGIN,
        "metro_probe",
    )
    assert ("refresh", "metro_stuck") in candidates(decision)
    free = stuck_state(NOON, busy=None)
    decision = decide(free, METRO_ALONE, NOON)
    assert isinstance(decision, Wait)
    assert ("metro", "in_metro") in candidates(decision)
    assert not any(c.params.get("resume") for c in decision.candidates)


def test_unconfirmed_exit_probed_with_main_at_30_min_and_2_hours() -> None:
    first = EXIT_AT + timedelta(minutes=31)
    decision = decide(stuck_state(first), METRO_ALONE, first)
    assert act(decision) == ("metro", {"probe": "main"})
    assert isinstance(decision, Act) and decision.reason == "metro_stuck"
    # Первая проверка сделана — следующая через 2 часа после итога.
    decision = decide(stuck_state(first), METRO_ALONE, first, metro_probes=["main"])
    assert isinstance(decision, Wait)
    assert decision.until == EXIT_AT + timedelta(hours=2) + TIMER_MARGIN
    second = EXIT_AT + timedelta(hours=2, minutes=1)
    decision = decide(stuck_state(second), METRO_ALONE, second, metro_probes=["main"])
    assert act(decision) == ("metro", {"probe": "main"})
    # Две проверки /main сделаны (третья — шаг самого забега): дальше — только к битве.
    later = EXIT_AT + timedelta(hours=3)
    decision = decide(stuck_state(later), METRO_ALONE, later, metro_probes=["main", "main"])
    assert isinstance(decision, Wait)
    assert decision.until == BATTLE_EVENING - timedelta(minutes=10) + TIMER_MARGIN


def test_unconfirmed_exit_checked_with_compact_10_min_before_battle() -> None:
    check = BATTLE_EVENING - timedelta(minutes=9)
    decision = decide(stuck_state(check), METRO_ALONE, check, metro_probes=["main", "main"])
    assert act(decision) == ("metro", {"probe": "compact"})
    # Проверка перед битвой важнее несделанной /main.
    decision = decide(stuck_state(check), METRO_ALONE, check, metro_probes=["main"])
    assert act(decision) == ("metro", {"probe": "compact"})
    # Проверка перед битвой без ответа: больше ничего не шлём до ответа игры.
    late = BATTLE_EVENING - timedelta(minutes=5)
    decision = decide(stuck_state(late), METRO_ALONE, late, metro_probes=["main", "compact"])
    assert isinstance(decision, Wait)
    assert ("refresh", "metro_stuck") in candidates(decision)
    assert not [w for w in candidates(decision) if w[1] == "chosen"]


def test_unconfirmed_exit_released_by_battle() -> None:
    # Выброс не распознан (или пропущен): после битвы забега персонаж точно снаружи.
    after = BATTLE_EVENING + timedelta(minutes=1)
    for probes in (["main", "compact"], []):
        decision = decide(stuck_state(after), METRO_ALONE, after, metro_probes=probes)
        assert act(decision)[0] == "refresh"
        assert not any(c.verdict == "metro_stuck" for c in decision.candidates)


def test_probe_cooldown_is_its_own() -> None:
    first = EXIT_AT + timedelta(minutes=31)
    held = {PROBE_KEY: first + timedelta(minutes=1), "metro": first + timedelta(hours=1)}
    decision = decide(stuck_state(first), METRO_ALONE, first, cooldowns=held)
    assert isinstance(decision, Wait) and decision.reason == f"cooldown:{PROBE_KEY}"
    decision = decide(stuck_state(first), METRO_ALONE, first, cooldowns={"metro": held["metro"]})
    assert act(decision) == ("metro", {"probe": "main"})


def test_confirmed_exit_lifts_hold() -> None:
    left = stuck_state(NOON, metro_message=Obs(value=None, at=NOON))
    decision = decide(left, METRO_ALONE, NOON)
    assert not any(c.verdict == "metro_stuck" for c in decision.candidates)
