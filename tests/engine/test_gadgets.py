"""Чистая логика гаджетов: деньги с акциями, правило крафтового сета, план покупки, вид заточки и
окна-запреты смены снаряжения."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.engine.gadget_catalog import SETS, SLOTS, SetKey, UpSlot, shop_item
from app.engine.gadgets import (
    BuyAction,
    WearSet,
    buy_plan,
    gear_guard,
    gear_until,
    in_dump_window,
    money_view,
    qualifies,
    set_of,
    set_score,
    shop_of,
    stocks_since,
    upgrade_kind,
)
from app.engine.settings import Settings
from app.engine.state.model import (
    CharacterState,
    GadgetsState,
    GadgetState,
    GorbushkaState,
    MetroRunRef,
    Obs,
    StockLimits,
    Upgrades,
)
from app.engine.state.reducer import StateReducer
from tests.engine.gadget_texts import INV, UNWEAR_P1, game_text
from tests.engine.inv_texts import INV_PROD_4
from tests.engine.state.helpers import PARSER

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)  # 12:00 MSK
BATTLE = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)  # 13:00 MSK
LIMITS = StockLimits(min_buy=1, max_sell=20, reserve=0, open_hour=8, close_hour=23)


def obs[T](value: T, at: datetime = NOW) -> Obs[T]:
    return Obs(value=value, at=at)


def on_settings(sets: Sequence[str] = (), **extra: Any) -> Settings:
    return Settings.model_validate(
        {"features": {"gadgets_buy": True}, "gadgets": {"sets": list(sets)}, **extra}
    )


def state_of(
    inv_text: str, *, level: int, money: int, company: str = "bmesa", at: datetime = NOW
) -> CharacterState:
    msg = game_text(inv_text, at=at)
    data = StateReducer().apply({}, msg, PARSER.parse(msg))
    return CharacterState.model_validate(data).model_copy(
        update={"level": obs(level, at), "money": obs(money, at), "company": obs(company, at)}
    )


def shop(code: str, *, grade: str | None = None, level: int | None = None) -> GadgetState:
    item = shop_item(code)
    assert item is not None
    return GadgetState(
        grade=grade,
        level=level,
        slot=SLOTS[item.slot].icon,
        name=item.name,
        bonuses=dict(item.bonuses),
        code=code,
    )


def part(key: SetKey, slot: UpSlot) -> GadgetState:
    crafted = SETS[key]
    item = crafted.items[slot][0]
    info = SLOTS[slot]
    return GadgetState(
        slot=info.icon,
        name=item.name,
        bonuses=dict(item.bonuses),
        code=f"{info.letter}{crafted.shop_tier or 15}",
    )


def full_set(key: SetKey) -> list[GadgetState]:
    return [part(key, slot) for slot in SETS[key].items]


RING = GadgetState(
    slot="💍", name="Простое кольцо", bonuses={"practice": 10, "wisdom": 10}, code="r10"
)
BOOK = GadgetState(slot="💻", name="Тетрадка", bonuses={"theory": 10, "cunning": 10}, code="b10")
LORAT = GadgetState(
    slot="👔", name="Жилетка LoRat", bonuses={"wisdom": 63, "theory": 23}, code="t501"
)


def char(
    worn: Sequence[GadgetState],
    bag: Sequence[GadgetState] = (),
    *,
    level: int,
    money: int,
    lines: Sequence[str] = (),
    used: int | None = None,
    cap: int = 24,
    **extra: Any,
) -> CharacterState:
    gadgets = GadgetsState(items=tuple(worn), sets=tuple(lines), bag=tuple(bag))
    return CharacterState(
        level=obs(level),
        money=obs(money),
        company=obs("bmesa"),
        bag=obs(len(bag) + 1 if used is None else used),
        bag_cap=obs(cap),
        gadgets=obs(gadgets),
        **extra,
    )


def newbie(*, used: int = 1, cap: int = 12, **extra: Any) -> CharacterState:
    """Аккаунт 2: уровень 14, $112, надеты 📱p4 и ⌚️w3, рюкзак 1/12."""
    return char([shop("p4"), shop("w3")], level=14, money=112, used=used, cap=cap, **extra)


WEAK = ("p6", "w6", "l6", "h6", "c6", "t6")


def weak(*skip: str) -> list[GadgetState]:
    return [shop(code) for code in WEAK if code[0] not in skip]


# --- правило сета


def test_set_of_by_name_and_shop_of_by_code_of_plain_item() -> None:
    assert set_of(shop("h11")) is SETS["summer"] and set_of(shop("h6")) is None
    assert set_of(LORAT) is None
    item = shop_of(shop("l2"))
    assert item is not None and (item.slot, item.tier) == ("legs", 2)
    assert shop_of(shop("l2", grade="⚪️", level=3)) is None and shop_of(LORAT) is None


def renamed(item: GadgetState) -> GadgetState:
    """Часть сета под другим названием в `/inv` (регистр, бренд), код тот же."""
    return item.model_copy(update={"name": f"Samsung {item.name.upper()}"})


def test_set_part_by_shop_code_when_name_differs() -> None:
    assert set_of(renamed(shop("p11"))) is SETS["summer"]
    assert set_of(renamed(shop("t14"))) is SETS["pig"]
    assert set_of(renamed(shop("p6"))) is None
    # Код чужого слота не в счёт: 📱 с кодом часов — не часть сета.
    assert set_of(renamed(shop("p11")).model_copy(update={"code": "w11"})) is None


def test_bought_part_with_other_name_is_not_bought_again() -> None:
    worn = [*weak("p"), renamed(shop("p11")), part("summer", "ring"), part("summer", "book")]
    bag = [renamed(shop("w11"))]
    plan = buy_plan(char(worn, bag, level=45, money=50_000), on_settings(["summer"]), 0, "x", NOW)
    assert plan.target is not None
    assert (plan.target.worn, plan.target.in_bag) == (("right",), ("left",))
    assert plan.action == BuyAction("set", "legs", 11, 44_499, False, 0)


def test_higher_rank_items_count_for_lower_set() -> None:
    # Ariah: Свинтус 3 + 2020 4 = 7, LoRat не засчитывается.
    y2020: list[UpSlot] = ["book", "ring", "left", "right"]
    worn = [part("pig", s) for s in ("head", "legs", "chest")]
    worn += [part("y2020", s) for s in y2020] + [LORAT]
    assert set_score(worn, SETS["pig"]) == 7
    assert set_score(worn, SETS["y2020"]) == 4
    assert qualifies(part("y2020", "book"), SETS["pig"])
    assert not qualifies(part("pig", "book"), SETS["y2020"])
    state = char(worn, level=50, money=0, lines=["🐷Сет Свинтус"])
    plan = buy_plan(state, on_settings(["pig"]), 0, "bmesa", NOW)
    assert plan.candidates == () and plan.target is None


# --- план покупки


def test_unknown_gadgets_give_no_action() -> None:
    state = newbie().model_copy(update={"gadgets": None})
    assert buy_plan(state, on_settings(), 0, "bmesa", NOW).verdict == "unknown"
    known = newbie()
    assert known.gadgets is not None
    doubtful = known.model_copy(
        update={"gadgets": known.gadgets.model_copy(update={"src": "doubtful"})}
    )
    plan = buy_plan(doubtful, on_settings(), 0, "bmesa", NOW)
    assert (plan.action, plan.verdict) == (None, "unknown")


def test_vip_account_with_empty_list_does_nothing() -> None:
    plan = buy_plan(state_of(INV, level=71, money=144), on_settings(), 0, "bmesa", NOW)
    assert (plan.action, plan.verdict) == (None, "no_upgrade")


def test_upgrade_set_keeps_empty_slot_empty() -> None:
    # Экран 34: 📱 снят, ⚫️Сет VIP в хвосте. Купить и надеть ⚪️0 — снять VIP: (a) не покупает.
    live = state_of(UNWEAR_P1, level=71, money=10**6)
    assert live.gadgets is not None and "⚫️Сет VIP" in live.gadgets.value.sets
    plan = buy_plan(live, on_settings(), 0, "bmesa", NOW)
    assert (plan.action, plan.verdict) == (None, "no_upgrade")
    worn = weak("p")
    for mark in ("⚫️Сет VIP", "🔴Сет Уникальный", "🔵Сет Редкий"):
        state = char(worn, level=49, money=10**6, lines=[mark])
        plan = buy_plan(state, on_settings(), 0, "x", NOW)
        assert (plan.action, plan.verdict) == (None, "no_upgrade")
    free = buy_plan(char(worn, level=49, money=10**6), on_settings(), 0, "x", NOW)
    assert free.action == BuyAction("empty", "right", 14, 59_999, True, 0)


def test_newbie_buys_best_affordable_allowed_for_empty_slots_only() -> None:
    # Уровень 14, $112, надеты p4 и w3: (a) для 👞 и 🕶 (👕/👔 — с 20); 👞 тир 2 за $79 (ур. 11),
    # тир 3 ($314) не по карману, тир 6 требует 15.
    plan = buy_plan(newbie(), on_settings(), 0, "bmesa", NOW)
    assert plan.action == BuyAction("empty", "legs", 2, 79, True, 0)
    assert plan.verdict == "chosen" and plan.money.available == 112


def test_empty_slot_takes_target_part_when_affordable() -> None:
    worn = [*weak("h"), part("summer", "ring"), part("summer", "book")]
    rich = buy_plan(char(worn, level=49, money=80_000), on_settings(["summer"]), 0, "x", NOW)
    # Тир 13 ($73 999) тоже по карману, но часть цели не придётся покупать дважды.
    assert rich.action == BuyAction("empty", "head", 11, 44_499, True, 0)
    poor = buy_plan(char(worn, level=49, money=40_000), on_settings(["summer"]), 0, "x", NOW)
    assert poor.action == BuyAction("empty", "head", 10, 19_899, True, 0)


def test_set_target_buys_cheapest_missing_part_into_bag() -> None:
    worn = [*weak(), part("summer", "ring"), part("summer", "book")]
    plan = buy_plan(char(worn, level=45, money=40_000), on_settings(["summer"]), 0, "x", NOW)
    assert plan.action == BuyAction("set", "right", 11, 31_999, False, 0)
    assert plan.target is not None and plan.target.status == "ready"
    assert plan.target.missing[:3] == (
        ("right", 11, 31_999),
        ("left", 11, 31_999),
        ("legs", 11, 44_499),
    )


def test_set_target_saving_reports_need_money() -> None:
    worn = [*weak(), part("summer", "ring"), part("summer", "book")]
    plan = buy_plan(char(worn, level=45, money=30_000), on_settings(["summer"]), 0, "x", NOW)
    assert (plan.action, plan.verdict) == (None, "saving")
    assert plan.target is not None
    assert (plan.target.status, plan.target.need_money) == ("saving", 1_999)


def test_higher_target_first_and_level_skips_it() -> None:
    worn = [*weak(), part("y2020", "ring"), part("y2020", "book")]
    plan = buy_plan(char(worn, level=45, money=0), on_settings(["summer", "pig"]), 0, "x", NOW)
    assert [(c.set, c.status) for c in plan.candidates] == [("pig", "level"), ("summer", "saving")]
    assert plan.target is not None and plan.target.set == "summer"


def test_all_parts_in_bag_gives_wear_set_of_remaining() -> None:
    worn = [
        *(part("summer", s) for s in ("right", "left", "legs", "ring", "book")),
        *(shop(c) for c in ("h6", "c6", "t6")),
    ]
    bag = [part("summer", s) for s in ("head", "chest", "torso")]
    plan = buy_plan(char(worn, bag, level=45, money=0), on_settings(["summer"]), 0, "x", NOW)
    assert plan.action == WearSet("summer", ("head", "chest", "torso"))
    assert plan.target is not None and plan.target.status == "wearing"
    assert plan.target.worn == ("right", "left", "legs")


def test_target_blocked_by_ring_and_book() -> None:
    # 💍/💻 вне каталога, все магазинные — тир 14 (лучше некуда): цель заблокирована, (c) пусто.
    worn = [shop(c) for c in ("p14", "w14", "l14", "h14", "c14", "t14")] + [RING, BOOK]
    plan = buy_plan(char(worn, level=49, money=10**6), on_settings(["summer"]), 0, "x", NOW)
    assert (plan.action, plan.verdict, plan.target) == (None, "target_blocked", None)
    assert plan.candidates[0].status == "blocked"
    assert plan.candidates[0].blocked_by == ("ring", "book")


def test_worn_set_without_line_is_worn_inactive() -> None:
    state = char(full_set("summer"), level=45, money=0, lines=["🗺Сет Кладоискатель"])
    plan = buy_plan(state, on_settings(["summer"]), 0, "x", NOW)
    assert [(c.set, c.status) for c in plan.candidates] == [("summer", "worn_inactive")]
    assert plan.target is None


def test_um_set_worn_is_unconfirmed_whatever_lines() -> None:
    for lines in ((), ("🌞Сет Летний", "🧪Сет Неведомый")):
        state = char(full_set("um"), level=46, money=0, lines=lines)
        plan = buy_plan(state, on_settings(["um"]), 0, "x", NOW)
        assert [(c.set, c.status) for c in plan.candidates] == [("um", "unconfirmed")]
        assert plan.target is None


def test_replace_picks_biggest_gain() -> None:
    # $300: 📱 T1 → T4 даёт +6, прочие — не больше +2.
    worn = [shop(c) for c in ("p1", "w3", "l1", "h1", "c1", "t1")]
    plan = buy_plan(char(worn, level=20, money=300), on_settings(), 0, "x", NOW)
    assert plan.action == BuyAction("replace", "right", 4, 299, True, 0)
    # Равный прирост +2: 📱 T3 → T4 за $299 и 👞 T1 → T2 за $79 — дешевле.
    worn = [shop(c) for c in ("p3", "w3", "l1", "h3", "c1", "t1")]
    plan = buy_plan(char(worn, level=20, money=300), on_settings(), 0, "x", NOW)
    assert plan.action == BuyAction("replace", "legs", 2, 79, True, 0)


def test_replace_compares_with_upgraded_bonus() -> None:
    # ⚪️6 Берцы с заточкой (+14, +8, +4 = 26) не хуже базы тира 7 (29)? Хуже — замена.
    worn = [*(shop(c) for c in ("p14", "w14", "h14", "c14", "t14"))]
    upgraded = GadgetState(
        grade="⚪️",
        level=6,
        slot="👞",
        name="Берцы",
        bonuses={"practice": 14, "cunning": 8, "theory": 4},
        code="l6",
    )
    plan = buy_plan(char([*worn, upgraded], level=27, money=6_000), on_settings(), 0, "x", NOW)
    assert plan.action == BuyAction("replace", "legs", 7, 5_219, True, 0)
    strong = upgraded.model_copy(update={"bonuses": {"practice": 20, "cunning": 10}})
    plan = buy_plan(char([*worn, strong], level=27, money=6_000), on_settings(), 0, "x", NOW)
    assert (plan.action, plan.verdict) == (None, "no_upgrade")


def test_replace_respects_upgrade_set_crafted_set_and_task() -> None:
    # Одна слабая вещь (📱 T1) среди тиров 14: без запретов — замена на T14.
    worn = [shop("p1"), *(shop(c) for c in ("w14", "l14", "h14", "c14", "t14"))]
    free = buy_plan(char(worn, level=49, money=10**6), on_settings(), 0, "x", NOW)
    assert free.action == BuyAction("replace", "right", 14, 59_999, True, 0)
    for mark in ("⚫️Сет VIP", "🔴Сет Уникальный", "🔵Сет Редкий"):
        state = char(worn, level=49, money=10**6, lines=[mark])
        plan = buy_plan(state, on_settings(), 0, "x", NOW)
        assert (plan.action, plan.verdict) == (None, "no_upgrade")
    task = {"gadget_upgrade": {"status": "active", "task_id": 3, "slot": "right"}}
    plan = buy_plan(char(worn, level=49, money=10**6), on_settings(**task), 0, "x", NOW)
    assert (plan.action, plan.verdict) == (None, "no_upgrade")
    # Летний сет горит: его части не меняются, хотя тир 14 сильнее.
    summer = full_set("summer")
    lit = char(summer, level=49, money=10**6, lines=["🌞Сет Летний"])
    assert buy_plan(lit, on_settings(), 0, "x", NOW).verdict == "no_upgrade"
    dark = buy_plan(char(summer, level=49, money=10**6), on_settings(), 0, "x", NOW)
    assert dark.action == BuyAction("replace", "chest", 14, 92_349, True, 0)
    # Живой экран: 🔴Сет Уникальный и 🌞Сет Летний — ничего не трогается.
    live = buy_plan(state_of(INV_PROD_4, level=49, money=10**6), on_settings(), 0, "x", NOW)
    assert (live.action, live.verdict) == (None, "no_upgrade")


def test_replace_keeps_slots_with_unknown_set_line() -> None:
    # Сет вне каталога (🗺 Кладоискатель, 🦉 Сова): из чего он собран, неизвестно — (c) не трогает
    # ни одного слота.
    worn = [shop("p1"), *(shop(c) for c in ("w14", "l14", "h14", "c14", "t14"))]
    for line in ("🗺Сет Кладоискатель", "🦉Сет Сова"):
        state = char(worn, level=49, money=10**6, lines=[line])
        plan = buy_plan(state, on_settings(), 0, "x", NOW)
        assert (plan.action, plan.verdict) == (None, "no_upgrade")
    # Знакомая строка крафтового сета (не надетого) замену не держит.
    state = char(worn, level=49, money=10**6, lines=["🗳Сет Логистик"])
    plan = buy_plan(state, on_settings(), 0, "x", NOW)
    assert plan.action == BuyAction("replace", "right", 14, 59_999, True, 0)


def test_replace_waits_for_money_when_better_tier_is_dear() -> None:
    worn = [shop(c) for c in ("p1", "w1", "l1", "h1", "c1", "t1")]
    plan = buy_plan(char(worn, level=20, money=2), on_settings(), 0, "x", NOW)
    assert (plan.action, plan.verdict) == (None, "cant_afford")


def test_bag_copy_is_worn_without_buying() -> None:
    # Отложенное окном надевание не покупает второй экземпляр: неулучшенный в рюкзаке.
    plan = buy_plan(newbie(), on_settings(), 0, "bmesa", NOW)
    assert plan.action == BuyAction("empty", "legs", 2, 79, True, 0)
    bought = newbie().model_copy(
        update={"gadgets": obs(GadgetsState(items=(shop("p4"), shop("w3")), bag=(shop("l2"),)))}
    )
    plan = buy_plan(bought, on_settings(), 0, "bmesa", NOW)
    assert plan.action == BuyAction("empty", "legs", 2, 0, True, 0, in_bag=True)
    upgraded = newbie().model_copy(
        update={
            "gadgets": obs(
                GadgetsState(
                    items=(shop("p4"), shop("w3")), bag=(shop("l2", grade="⚪️", level=3),)
                )
            )
        }
    )
    plan = buy_plan(upgraded, on_settings(), 0, "bmesa", NOW)
    assert plan.action == BuyAction("empty", "legs", 2, 79, True, 0)
    # (c): лучший тир уже лежит в рюкзаке — надевается без покупки.
    worn = [shop(c) for c in ("p1", "w3", "l1", "h1", "c1", "t1")]
    state = char(worn, [shop("p4")], level=20, money=300)
    plan = buy_plan(state, on_settings(), 0, "x", NOW)
    assert plan.action == BuyAction("replace", "right", 4, 0, True, 0, in_bag=True)


def test_bag_full_blocks_buying_not_wearing() -> None:
    plan = buy_plan(newbie(used=24, cap=23), on_settings(), 0, "bmesa", NOW)
    assert (plan.action, plan.verdict) == (None, "bag_full")
    worn = [
        *(part("summer", s) for s in ("right", "left", "legs", "ring", "book")),
        *(shop(c) for c in ("h6", "c6", "t6")),
    ]
    bag = [part("summer", s) for s in ("head", "chest", "torso")]
    state = char(worn, bag, level=45, money=0, used=24, cap=23)
    plan = buy_plan(state, on_settings(["summer"]), 0, "x", NOW)
    assert plan.action == WearSet("summer", ("head", "chest", "torso"))
    # Копия в рюкзаке надевается и при полном рюкзаке.
    full = newbie(used=24, cap=23).model_copy(
        update={"gadgets": obs(GadgetsState(items=(shop("p4"), shop("w3")), bag=(shop("l2"),)))}
    )
    plan = buy_plan(full, on_settings(), 0, "bmesa", NOW)
    assert plan.action == BuyAction("empty", "legs", 2, 0, True, 0, in_bag=True)


# --- деньги


def market(at: datetime = NOW, **fields: Any) -> dict[str, Any]:
    values = {
        "stock_holdings": {"bmesa": 100, "piper": 10, "hooli": 3},
        "stock_quotes": {"bmesa": 5, "piper": 11, "hooli": 25},
        "stock_limits": LIMITS,
    }
    battle = {"battle_at": Obs(value=BATTLE, at=NOW - timedelta(hours=1))}
    return {**battle, **{k: obs(v, fields.get(k, at)) for k, v in values.items()}}


def test_money_counts_foreign_stocks_minus_fee_under_max_sell() -> None:
    # Своя bmesa не считается; hooli дороже лимита продажи; piper: 10 × (11 − 1).
    state = CharacterState(money=obs(500), **market())
    view = money_view(state, 150, "bmesa", NOW)
    assert (view.cash, view.stocks, view.reserve, view.available, view.stale) == (
        500,
        100,
        150,
        450,
        None,
    )


def test_money_ignores_stocks_when_own_company_unknown() -> None:
    view = money_view(CharacterState(money=obs(500), **market()), 0, None, NOW)
    assert (view.stocks, view.available, view.stale) == (0, 500, None)
    unknown = money_view(CharacterState(), 0, "bmesa", NOW)
    assert (unknown.cash, unknown.available, unknown.stale) == (None, None, "stock_holdings")


def test_stocks_seen_before_passed_battle_are_stale() -> None:
    after = BATTLE + timedelta(minutes=30)
    assert stocks_since(CharacterState(**market()), after) == BATTLE
    # Битва впереди: граница — следующая битва минус 9 ч.
    assert stocks_since(CharacterState(**market()), NOW) == BATTLE - timedelta(hours=9)
    assert stocks_since(CharacterState(), NOW) is None
    before = BATTLE - timedelta(minutes=10)
    old = CharacterState(money=obs(500), **market(before))
    view = money_view(old, 0, "bmesa", after)
    assert (view.stocks, view.available, view.stale) == (0, 500, "stock_holdings")
    quotes = CharacterState(money=obs(500), **market(after, stock_quotes=before))
    assert money_view(quotes, 0, "bmesa", after).stale == "stock_quotes"
    fresh = CharacterState(money=obs(500), **market(before))
    assert money_view(fresh, 0, "bmesa", before).stale is None


def test_sell_needed_is_cash_shortfall_over_reserve() -> None:
    # $50 наличных, резерв $20, акции piper на $100: тир 2 за $79 — продать на $49.
    state = newbie(**market()).model_copy(update={"money": obs(50)})
    plan = buy_plan(state, on_settings(), 20, "bmesa", NOW)
    assert plan.money.available == 130
    assert plan.action == BuyAction("empty", "legs", 2, 79, True, 49)


# --- заточка


@pytest.mark.parametrize(
    ("kind", "level", "stocks", "expected"),
    [
        ("auto", 6, Upgrades(white=5, blue=1, red=1), "white"),
        ("auto", 7, Upgrades(white=5, blue=1, red=1), "red"),
        ("auto", 7, Upgrades(white=5, blue=1, red=0), "blue"),
        ("auto", 3, Upgrades(white=0, blue=1, red=1), "red"),
        ("auto", 30, Upgrades(white=9, blue=0, red=0), None),
        ("blue", 3, Upgrades(white=9, blue=0, red=9), None),
        ("red", 3, Upgrades(white=9, blue=0, red=1), "red"),
    ],
)
def test_upgrade_kind(kind: str, level: int, stocks: Upgrades, expected: str | None) -> None:
    assert upgrade_kind(kind, level, stocks, 7) == expected


# --- окна-запреты


def battle_state(**extra: Any) -> CharacterState:
    return CharacterState(battle_at=Obs(value=BATTLE, at=NOW - timedelta(hours=1)), **extra)


def gorbushka(state: str, fight: datetime, ticket: datetime = NOW + timedelta(hours=1)) -> Any:
    return obs(GorbushkaState(state=state, next_fight_at=fight, ticket_until=ticket))


def test_gear_guard_windows() -> None:
    metro = obs(MetroRunRef(message_id=7, battle_at=Obs(value=BATTLE, at=NOW)))
    assert gear_guard(battle_state(metro_message=metro), NOW) == ("in_metro", None)
    met = NOW - timedelta(seconds=30)
    meeting = battle_state(gorbushka=gorbushka("meeting", met))
    assert gear_guard(meeting, NOW) == ("gorbushka_meeting", met)
    for fight in (NOW + timedelta(minutes=1), NOW - timedelta(minutes=1)):
        waiting = battle_state(gorbushka=gorbushka("waiting", fight))
        assert gear_guard(waiting, NOW) == ("gorbushka_meeting", fight)
    later = battle_state(gorbushka=gorbushka("waiting", NOW + timedelta(minutes=5)))
    assert gear_guard(later, NOW) is None
    # Билет истёк — встречи не будет.
    expired = gorbushka("waiting", NOW - timedelta(minutes=1), NOW - timedelta(minutes=2))
    assert gear_guard(battle_state(gorbushka=expired), NOW) is None
    end = BATTLE + timedelta(minutes=1)
    for moment in (BATTLE - timedelta(minutes=5), BATTLE + timedelta(seconds=30)):
        assert gear_guard(battle_state(), moment) == ("battle_window", end)
    assert gear_guard(battle_state(), BATTLE - timedelta(minutes=7)) is None
    assert gear_guard(battle_state(), end) is None


def test_gear_until_is_nearest_window_start() -> None:
    assert gear_until(battle_state(), NOW) == BATTLE - timedelta(minutes=6)
    soon = battle_state(gorbushka=gorbushka("waiting", NOW + timedelta(minutes=30)))
    assert gear_until(soon, NOW) == NOW + timedelta(minutes=28)
    inside = BATTLE - timedelta(minutes=3)
    assert gear_until(battle_state(), inside) == inside
    assert gear_until(CharacterState(), NOW) is None


def test_dump_window() -> None:
    # Слив за 5 мин до битвы, окно предпроверки — ещё 10 мин раньше, до минуты до битвы.
    settings = on_settings()
    state = battle_state()
    minutes = {16: False, 15: True, 2: True, 1: False}
    for before, inside in minutes.items():
        assert in_dump_window(state, settings, BATTLE - timedelta(minutes=before)) is inside
    assert not in_dump_window(CharacterState(), settings, NOW)
    off = on_settings(features={"gadgets_buy": True, "stocks_dump": False})
    assert not in_dump_window(state, off, BATTLE - timedelta(minutes=2))
