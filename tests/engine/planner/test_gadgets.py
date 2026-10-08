"""Шаги гаджетов планировщика: надеть сет, купить гаджет (перед делами), порция заточки (после
дел); резерв покупки, обновления, окна-запреты и пробуждения."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from app.engine.gadgets import UPGRADE_BATCH, gear_until
from app.engine.planner.base import TIMER_MARGIN
from app.engine.planner.decide import _Planner, decide, outlook
from app.engine.planner.gadgets import buy_view
from app.engine.planner.types import Act, Wait, Wakeup
from app.engine.settings import GadgetUpgradeSection, Settings
from app.engine.state.model import (
    BusyState,
    CharacterState,
    GadgetsState,
    GadgetState,
    GorbushkaState,
    Obs,
    StockLimits,
    Upgrades,
)
from tests.engine.planner.test_decide import NOW, act, awake, config, m, verdicts, w
from tests.engine.test_gadgets import part, shop, weak

AFTER_BATTLE = NOW + timedelta(hours=1, minutes=1) + TIMER_MARGIN
LIMITS = StockLimits(min_buy=1, max_sell=20, reserve=0, open_hour=8, close_hour=23)
# Портфель: своя bmesa не считается, piper — 10 × (11 − 1) = $100.
MARKET = {
    "stock_holdings": {"bmesa": 100, "piper": 10},
    "stock_quotes": {"bmesa": 5, "piper": 11},
    "stock_limits": LIMITS,
}
NEWBIE = [shop("p4"), shop("w3")]
LEGS2 = {
    "rule": "empty",
    "slot": "legs",
    "tier": 2,
    "price": 79,
    "reserve": 0,
    "wear": True,
    "in_bag": False,
}


def flags(sets: Sequence[str] = (), *, sleep: bool = False, **data: Any) -> Settings:
    """Покупка включена; сон выключен, чтобы резерв отеля не мешал считать деньги."""
    features = {"gadgets_buy": True, "sleep": sleep, **data.pop("features", {})}
    gadgets = {"sets": list(sets), **data.pop("gadgets", {})}
    return config({**data, "features": features, "gadgets": gadgets})


def gear(
    worn: Sequence[GadgetState],
    bag: Sequence[GadgetState] = (),
    *,
    used: int | None = None,
    cap: int = 24,
    **over: Any,
) -> CharacterState:
    fields: dict[str, Any] = {
        "gadgets": GadgetsState(items=tuple(worn), bag=tuple(bag)),
        "bag": len(bag) + 1 if used is None else used,
        "bag_cap": cap,
        "company": "bmesa",
        # Биржа свежая: иначе шаг покупки сначала открыл бы /stock.
        **market(),
    }
    return awake(**{**fields, **over})


def newbie(money: int = 112, **over: Any) -> CharacterState:
    """Уровень 14, надеты 📱p4 и ⌚️w3, рюкзак 1/12: лучший по карману — 👞 тир 2 за $79."""
    return gear(NEWBIE, level=14, money=money, used=1, cap=12, **over)


def market(at: datetime = NOW) -> dict[str, Obs[Any]]:
    return {k: Obs(value=v, at=at) for k, v in MARKET.items()}


def planner(state: CharacterState, cfg: Settings, **kw: Any) -> _Planner:
    return _Planner(
        state,
        cfg,
        kw.pop("now", NOW),
        None,
        kw.pop("last_refresh", {}),
        {},
        {},
    )


def gadget_verdicts(decision: Act | Wait) -> dict[str, str]:
    return {k: v for k, v in verdicts(decision).items() if k.startswith("gadget_")}


def task(slot: str = "head", **over: Any) -> GadgetUpgradeSection:
    data = {
        "status": "active",
        "task_id": 3,
        "slot": slot,
        "gadget": shop("h6").name,
        "kind": "auto",
        "target": 25,
        "start_level": 0,
        "started_at": NOW - timedelta(hours=1),
        **over,
    }
    return GadgetUpgradeSection.model_validate(data)


def upgrading(cfg: Settings, **over: Any) -> Settings:
    return cfg.model_copy(update={"gadget_upgrade": task(**over)})


# --- порядок и флаг


def test_step_order() -> None:
    names = [s.__name__ for s in planner(awake(), flags()).steps()]
    assert names[names.index("trip") :] == [
        "trip",
        "startup",
        "gadget_wear_set",
        "gadget_buy",
        "deeds",
        "gadget_upgrade",
    ]


def test_buy_off_by_default() -> None:
    off = flags(features={"gadgets_buy": False})
    decision = decide(newbie(), off, NOW)
    assert act(decision)[0] == "deed:job"
    assert gadget_verdicts(decision) == {}


def test_buy_chosen_with_params() -> None:
    decision = decide(newbie(), flags(), NOW)
    assert act(decision) == ("gadget_buy", LEGS2)
    assert isinstance(decision, Act) and decision.reason == "empty legs2"
    # Покупка — перед делами.
    assert "deed:job" not in verdicts(decision)


def test_bag_copy_is_worn_without_purchase_params() -> None:
    bag = [shop("l2")]
    state = gear(NEWBIE, bag, level=14, money=0, used=2, cap=12)
    params = {**LEGS2, "price": 0, "in_bag": True}
    assert act(decide(state, flags(), NOW)) == ("gadget_buy", params)


def test_stale_bag_or_doubtful_gadgets_refresh_inventory() -> None:
    inventory = ("refresh", {"source": "inventory"})
    # После покупки (`GadgetBought`) список рюкзака сомнителен: план ждёт свежий /inv.
    bought = Obs(value=GadgetsState(items=tuple(NEWBIE)), at=NOW, src="doubtful")
    decision = decide(newbie(gadgets=bought), flags(), NOW)
    assert act(decision) == inventory
    assert verdicts(decision)["gadget_buy"] == "stale:gadgets"
    unknown_bag = newbie().model_copy(update={"bag": None})
    decision = decide(unknown_bag, flags(), NOW)
    assert act(decision) == inventory
    assert verdicts(decision)["gadget_buy"] == "stale:bag"
    old_cap = newbie(bag_cap=Obs(value=12, at=m(-7 * 60)))
    assert act(decide(old_cap, flags(), NOW)) == inventory
    # Рестарт снимок не освежает: давний /inv — тоже обновить.
    old = newbie(gadgets=Obs(value=GadgetsState(items=tuple(NEWBIE)), at=m(-7 * 60)))
    assert act(decide(old, flags(), NOW)) == inventory


def test_gadget_reserve_is_keep_money_ticket_and_hotel() -> None:
    need_ticket = GorbushkaState(state="need_ticket")
    cfg = flags(sleep=True, gadgets={"keep_money": 100})
    p = planner(newbie(money=1000, gorbushka=need_ticket), cfg)
    # Билет Горбушки $120, отель — 3💵 за уровень (цена ещё не видена).
    assert p.gadget_reserve() == 100 + 120 + 42
    assert planner(newbie(money=1000), flags()).gadget_reserve() == 0


def test_unknown_reserve_refreshes_gorbushka_or_profile() -> None:
    stale = Obs(value=GorbushkaState(state="done"), at=m(-7 * 60))
    p = planner(newbie(gorbushka=stale), flags())
    assert p.reserve_unknown() == "gorbushka"
    assert p.gadget_reserve() is None
    assert act(p.gadget_buy(None)) == ("refresh", {"source": "gorbushka"})
    assert verdicts(p.wait())["gadget_buy"] == "stale:gorbushka"
    # Горбушка выключена — её билет не резервируется и не нужен.
    off = flags(features={"gorbushka": False})
    assert planner(newbie(gorbushka=stale), off).reserve_unknown() is None
    unknown = Obs(value=NOW, at=NOW, src="doubtful")
    p = planner(newbie(sleep_deadline=unknown), flags(sleep=True))
    assert p.reserve_unknown() == "sleep_deadline"
    assert act(p.gadget_buy(None)) == ("refresh", {"source": "profile"})
    assert buy_view(newbie(sleep_deadline=unknown), flags(sleep=True), NOW) is None
    # Сон выключен — отель не резервируется.
    assert planner(newbie(sleep_deadline=unknown), flags()).reserve_unknown() is None


def test_buy_view_uses_planner_reserve() -> None:
    view = buy_view(newbie(money=1000), flags(gadgets={"keep_money": 950}), NOW)
    assert view is not None and view.money.reserve == 950
    # $50 наличных сверх резерва и акции piper на $100: тир 2 за $79.
    assert view.action is not None and view.action.price == 79


def test_sale_needed_with_stale_stocks_refreshes_stocks() -> None:
    # Наличных $5 — даже тир 1 ($9) без акций не по карману; акции сняты до прошлой битвы.
    old = market(m(-10 * 60))
    decision = decide(newbie(money=5, **old), flags(), NOW)
    assert act(decision) == ("refresh", {"source": "stocks"})
    assert verdicts(decision)["gadget_buy"] == "stale:stock_holdings"
    # Свежие акции: продажа piper ($100) покрывает нехватку и до тира 2.
    decision = decide(newbie(money=5, **market()), flags(), NOW)
    assert act(decision) == ("gadget_buy", LEGS2)


def test_stale_stocks_are_read_before_spending_cash() -> None:
    # Наличных хватает на тир 1, но с акциями мог бы выйти тир получше: сначала /stock.
    old = market(m(-600))
    decision = decide(newbie(money=10, **old), flags(), NOW)
    assert act(decision) == ("refresh", {"source": "stocks"})
    assert verdicts(decision)["gadget_buy"] == "stale:stock_holdings"
    tier1 = ("gadget_buy", {**LEGS2, "tier": 1, "price": 9})
    # /stock уже открывали после битвы, а поля не обновились — покупка на наличные.
    p = planner(newbie(money=10, **old), flags(), last_refresh={"stocks": m(-30)})
    assert act(p.gadget_buy(None)) == tier1
    # Биржа закрыта — покупка на наличные идёт и ночью, без /stock.
    closed = LIMITS.model_copy(update={"open_hour": 14})
    shut = newbie(money=10, **{**old, "stock_limits": Obs(value=closed, at=m(-600))})
    p = planner(shut, flags())
    assert act(p.gadget_buy(None)) == tier1
    assert p.wakeups == []
    # Надеть экземпляр из рюкзака денег не тратит: биржа не нужна.
    bag = gear(NEWBIE, [shop("l2")], level=14, money=0, used=2, cap=12, **old)
    assert act(planner(bag, flags()).gadget_buy(None))[1]["in_bag"] is True


def test_saving_with_stale_stocks_refreshes_stocks() -> None:
    worn = [*weak(), part("summer", "ring"), part("summer", "book")]
    state = gear(worn, level=45, money=30_000, **market(m(-10 * 60)))
    p = planner(state, flags(["summer"]))
    assert act(p.gadget_buy(None)) == ("refresh", {"source": "stocks"})


def test_stocks_screen_not_updating_does_not_loop() -> None:
    # /stock уже открывали после прошлой битвы, а поля не обновились: второй раз — не поможет.
    state = newbie(money=5, **market(m(-10 * 60)))
    p = planner(state, flags(), last_refresh={"stocks": m(-30)})
    assert p.gadget_buy(None) is None
    assert gadget_verdicts(p.wait()) == {"gadget_buy": "cant_afford"}


def test_market_closed_wakes_at_open_hour() -> None:
    # 13:00 MSK, биржа с 14 до 23: продажа нужна (наличных $50, резерв $20, тир 2 за $79).
    closed = LIMITS.model_copy(update={"open_hour": 14})
    state = newbie(money=50, **{**market(), "stock_limits": Obs(value=closed, at=NOW)})
    p = planner(state, flags(gadgets={"keep_money": 20}))
    assert p.gadget_buy(None) is None
    assert gadget_verdicts(p.wait()) == {"gadget_buy": "market_closed"}
    assert p.wakeups == [Wakeup(w(60), "market_open")]
    # После закрытия — к открытию завтра.
    late = LIMITS.model_copy(update={"close_hour": 12})
    state = newbie(money=50, **{**market(), "stock_limits": Obs(value=late, at=NOW)})
    p = planner(state, flags(gadgets={"keep_money": 20}))
    assert p.gadget_buy(None) is None
    assert p.wakeups == [Wakeup(w(19 * 60), "market_open")]
    # Покупка на наличные идёт и при закрытой бирже.
    state = newbie(money=500, **{**market(), "stock_limits": Obs(value=closed, at=NOW)})
    assert act(planner(state, flags()).gadget_buy(None))[0] == "gadget_buy"


def test_market_closed_with_stale_stocks_waits_without_refresh() -> None:
    closed = LIMITS.model_copy(update={"open_hour": 14})
    old = {**market(m(-10 * 60)), "stock_limits": Obs(value=closed, at=m(-10 * 60))}
    p = planner(newbie(money=5, **old), flags())
    assert p.gadget_buy(None) is None
    assert gadget_verdicts(p.wait()) == {"gadget_buy": "market_closed"}
    assert p.wakeups == [Wakeup(w(60), "market_open")]


BATTLE = NOW + timedelta(hours=1)


def before_battle(minutes: float) -> datetime:
    return BATTLE - timedelta(minutes=minutes)


def battle_in(minutes: float) -> dict[str, Any]:
    """Состояние снято за `minutes` до битвы (битва — в начале часа); решение — в тот же момент."""
    return {"at": before_battle(minutes), "battle_at": BATTLE}


def test_dump_window_rejects() -> None:
    # Битва через 10 мин, слив за 5: окно предпроверки слива — любая покупка ждёт, даже без
    # продажи акций.
    worn = [*weak(), part("summer", "ring"), part("summer", "book")]
    state = gear(worn, level=45, money=40_000, **battle_in(10))
    dump = flags(["summer"], features={"stocks_dump": True})
    p = planner(state, dump, now=before_battle(10))
    assert p.gadget_buy(None) is None
    assert gadget_verdicts(p.wait()) == {"gadget_buy": "dump_window"}
    assert Wakeup(BATTLE + timedelta(minutes=1) + TIMER_MARGIN, "battle") in p.wakeups
    # Слив выключен — окна нет.
    off = planner(state, flags(["summer"]), now=before_battle(10))
    assert act(off.gadget_buy(None))[0] == "gadget_buy"


def test_gear_guard_rejects_and_wakes_after_battle() -> None:
    # За 5 мин до битвы: покупка с надеванием — окно битвы, пробуждение к его концу.
    p = planner(newbie(**battle_in(5)), flags(), now=before_battle(5))
    assert p.gadget_buy(None) is None
    assert gadget_verdicts(p.wait()) == {"gadget_buy": "battle_window"}
    assert Wakeup(AFTER_BATTLE, "gear_guard") in p.wakeups
    # Покупка в рюкзак (цель-сет) тоже ждёт: сценарий откажет в окне, а продажа акций до отказа
    # унесла бы наличные в битву.
    worn = [*weak(), part("summer", "ring"), part("summer", "book")]
    state = gear(worn, level=45, money=40_000, **battle_in(5))
    p = planner(state, flags(["summer"]), now=before_battle(5))
    assert p.gadget_buy(None) is None
    assert gadget_verdicts(p.wait()) == {"gadget_buy": "battle_window"}
    assert Wakeup(AFTER_BATTLE, "gear_guard") in p.wakeups


def test_gorbushka_meeting_rejects_without_past_wakeup() -> None:
    met = GorbushkaState(state="meeting", next_fight_at=m(-1), ticket_until=m(60), won=1, total=4)
    p = planner(newbie(gorbushka=met), flags(features={"gorbushka": False}))
    assert p.gadget_buy(None) is None
    assert gadget_verdicts(p.wait()) == {"gadget_buy": "gorbushka_meeting"}
    assert all(wake.at > NOW for wake in p.wakeups)


# --- надеть сет

WEAR_WORN = [
    *(part("summer", s) for s in ("right", "left", "legs", "ring", "book")),
    *(shop(c) for c in ("h6", "c6", "t6")),
]
WEAR_BAG = [part("summer", s) for s in ("head", "chest", "torso")]


def wearing(**over: Any) -> CharacterState:
    return gear(WEAR_WORN, WEAR_BAG, level=45, money=0, **over)


def test_wear_set_before_buy() -> None:
    decision = decide(wearing(), flags(["summer"]), NOW)
    params = {"set": "summer", "slots": ["head", "chest", "torso"]}
    assert act(decision) == ("gadget_wear_set", params)


def test_wear_set_blocked_by_running_upgrade_on_slot() -> None:
    cfg = upgrading(flags(["summer"]), slot="head")
    p = planner(wearing(), cfg)
    assert p.gadget_wear_set(None) is None
    assert gadget_verdicts(p.wait()) == {"gadget_wear_set": "upgrade_running"}
    # Заточка на слоте вне сета не мешает.
    other = upgrading(flags(["summer"]), slot="ring", gadget=part("summer", "ring").name)
    assert act(planner(wearing(), other).gadget_wear_set(None))[0] == "gadget_wear_set"


def test_wear_set_waits_for_gear_guard_and_fresh_inventory() -> None:
    p = planner(wearing(**battle_in(3)), flags(["summer"]), now=before_battle(3))
    assert p.gadget_wear_set(None) is None
    assert gadget_verdicts(p.wait()) == {"gadget_wear_set": "battle_window"}
    assert Wakeup(AFTER_BATTLE, "gear_guard") in p.wakeups
    seen = GadgetsState(items=tuple(WEAR_WORN), bag=tuple(WEAR_BAG))
    stale = wearing(gadgets=Obs(value=seen, at=NOW, src="doubtful"))
    assert planner(stale, flags(["summer"])).gadget_wear_set(None) is None


# --- заточка

# Покупка выключена: заточке она не нужна (а без биржи шаг покупки обновлял бы /stock).
UPGRADE_ONLY = flags(features={"gadgets_buy": False})


def test_upgrade_step_after_deeds_and_while_busy() -> None:
    worn = [*weak(), part("summer", "ring")]
    cfg = upgrading(UPGRADE_ONLY)
    deed = BusyState(activity="job", until=m(20))
    busy = gear(worn, level=45, busy=deed)
    decision = decide(busy, cfg, NOW)
    until = gear_until(busy, NOW)
    assert until is not None
    assert act(decision) == (
        "gadget_upgrade",
        {
            "task_id": 3,
            "slot": "head",
            "gadget": shop("h6").name,
            "target": 25,
            "kind": "auto",
            "white_until": 7,
            "batch": UPGRADE_BATCH,
            "until": until.isoformat(),
        },
    )
    # Свободен и есть 🔥 — сначала дело; без 🔥 — заточка.
    assert act(decide(gear(worn, level=45), cfg, NOW))[0] == "deed:job"
    tired = gear(worn, level=45, motivation=0)
    assert act(decide(tired, cfg, NOW))[0] == "gadget_upgrade"


def test_upgrade_done_task_or_idle_has_no_step() -> None:
    worn = [*weak(), part("summer", "ring")]
    tired = gear(worn, level=45, motivation=0)
    for status in ("idle", "done", "stopped"):
        decision = decide(tired, upgrading(UPGRADE_ONLY, status=status), NOW)
        assert "gadget_upgrade" not in verdicts(decision)


def test_upgrade_not_decided_while_sleeping() -> None:
    sleeping = gear(weak(), level=45, busy=BusyState(activity="sleep_hotel", until=m(300)))
    decision = decide(sleeping, upgrading(UPGRADE_ONLY), NOW)
    assert isinstance(decision, Wait)
    assert gadget_verdicts(decision) == {}


def test_upgrade_waits_for_gear_guard() -> None:
    tired = gear(weak(), level=45, motivation=0, **battle_in(3))
    p = planner(tired, upgrading(UPGRADE_ONLY), now=before_battle(3))
    assert p.gadget_upgrade(None) is None
    assert gadget_verdicts(p.wait()) == {"gadget_upgrade": "battle_window"}
    assert Wakeup(AFTER_BATTLE, "gear_guard") in p.wakeups


def test_zero_stocks_snapshot_does_not_stop_upgrade_step() -> None:
    empty = Upgrades(white=0, blue=0, red=0)
    tired = gear(weak(), level=45, motivation=0, upgrades=empty)
    assert act(decide(tired, upgrading(UPGRADE_ONLY), NOW))[0] == "gadget_upgrade"


def test_gear_guard_wakeup_passed_in_sleep_is_dropped() -> None:
    # Конец окна-запрета, наступивший во сне, к подъёму теряет смысл.
    asleep = BusyState(activity="sleep_hotel", until=BATTLE + timedelta(hours=5))
    sleeping = gear(weak(), level=45, busy=asleep, **battle_in(3))
    view = outlook(sleeping, upgrading(UPGRADE_ONLY), before_battle(3))
    assert all(t.kind != "gear_guard" for t in view.after_wake)
