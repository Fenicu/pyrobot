"""Гаджеты: деньги на покупку с акциями чужих компаний, правило крафтового сета, план покупки, вид
заточки и окна-запреты смены снаряжения — чистые функции над состоянием и настройками."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from app.engine.gadget_catalog import (
    SET_MIN_SLOTS,
    SETS,
    SHOP,
    SLOTS,
    CraftedSet,
    SetKey,
    ShopItem,
    ShopSlot,
    UpgradeKind,
    UpSlot,
    set_by_name,
    shop_item,
    slot_of_icon,
)
from app.engine.planner.base import BATTLE_AFTER, BATTLE_BEFORE, battle_hour
from app.engine.planner.obligations import DUMP_SPAN, TARGET_LAST_CALL, metro_inside
from app.engine.settings import Settings
from app.engine.state.model import CharacterState, GadgetsState, GadgetState, Obs, Upgrades

UPGRADE_BATCH = 20
GORBUSHKA_GUARD = timedelta(minutes=2)
MIN_BATTLE_GAP = timedelta(hours=9)
# Строки сетов заточки в `/inv`: сет считается по всем надетым, замена любого слота его снимет.
UPGRADE_SET_MARKS = ("⚫️", "🔴", "🔵")
_VS16 = "️"
_SHOP_SLOTS: tuple[ShopSlot, ...] = tuple(SHOP)
_EXTRA_SLOTS: tuple[UpSlot, ...] = ("ring", "book")
_STOCK_FIELDS = ("stock_holdings", "stock_quotes", "stock_limits")

TargetStatus = Literal[
    "saving", "ready", "wearing", "blocked", "worn_inactive", "unconfirmed", "level"
]
_ACTIVE: tuple[TargetStatus, ...] = ("saving", "ready", "wearing")


def up_slot(item: GadgetState) -> UpSlot | None:
    info = slot_of_icon(item.slot)
    return None if info is None else info.up


def set_of(item: GadgetState) -> CraftedSet | None:
    found = set_by_name(item.name)
    if found is None or found[1] != up_slot(item):
        return None
    return found[0]


def shop_of(item: GadgetState) -> ShopItem | None:
    if item.code is None or item.grade is not None:
        return None
    return shop_item(item.code)


def qualifies(item: GadgetState, target: CraftedSet) -> bool:
    own = set_of(item)
    return own is not None and own.rank >= target.rank


def set_score(worn: Sequence[GadgetState], target: CraftedSet) -> int:
    return len({up_slot(item) for item in worn if qualifies(item, target)})


def current_bonus(item: GadgetState) -> int:
    return sum(item.bonuses.values())


def base_bonus(item: ShopItem) -> int:
    return sum(item.bonuses.values())


# --- деньги


@dataclass(frozen=True)
class MoneyView:
    cash: int | None
    stocks: int
    reserve: int
    available: int | None
    stale: str | None


def stocks_since(state: CharacterState, now: datetime) -> datetime | None:
    """С какого момента портфель, котировки и лимиты годны: прошедшая битва, а пока она впереди —
    следующая минус `MIN_BATTLE_GAP`."""
    seen = state.battle_at
    if seen is None:
        return None
    battle = battle_hour(seen.value, seen.at)
    return battle if battle <= now else battle - MIN_BATTLE_GAP


def _usable(seen: Obs[Any] | None, since: datetime | None) -> bool:
    if seen is None or seen.src == "doubtful":
        return False
    return since is None or seen.at >= since


def money_view(state: CharacterState, reserve: int, own: str | None, now: datetime) -> MoneyView:
    seen = state.money
    cash = None if seen is None or seen.src == "doubtful" else seen.value
    stocks, stale = 0, None
    if own is not None:
        since = stocks_since(state, now)
        stale = next((f for f in _STOCK_FIELDS if not _usable(getattr(state, f), since)), None)
        holdings, quotes, limits = state.stock_holdings, state.stock_quotes, state.stock_limits
        if stale is None and holdings and quotes and limits:
            stocks = sum(
                qty * (price - 1)
                for company, qty in holdings.value.items()
                if company != own
                and (price := quotes.value.get(company)) is not None
                and 1 < price <= limits.value.max_sell
            )
    available = None if cash is None else cash + stocks - reserve
    return MoneyView(cash, stocks, reserve, available, stale)


def in_dump_window(state: CharacterState, settings: Settings, now: datetime) -> bool:
    """Окно предпроверки слива налички (`stocks_dump`): в нём акции не продаются, чтобы не
    продать только что купленное."""
    seen = state.battle_at
    if seen is None:
        return False
    battle = battle_hour(seen.value, seen.at)
    lead = timedelta(minutes=settings.stocks.dump_lead_min)
    return battle - lead - DUMP_SPAN <= now < battle - TARGET_LAST_CALL


# --- план покупки


@dataclass(frozen=True)
class BuyAction:
    rule: Literal["empty", "set", "replace"]
    slot: ShopSlot
    tier: int
    price: int
    wear: bool
    sell_needed: int
    # Неулучшенный экземпляр уже в рюкзаке: надеть без покупки.
    in_bag: bool = False


@dataclass(frozen=True)
class WearSet:
    set: SetKey
    slots: tuple[UpSlot, ...]


@dataclass(frozen=True)
class TargetView:
    set: SetKey
    status: TargetStatus
    missing: tuple[tuple[ShopSlot, int, int], ...]
    in_bag: tuple[UpSlot, ...]
    worn: tuple[UpSlot, ...]
    blocked_by: tuple[UpSlot, ...]
    need_money: int


@dataclass(frozen=True)
class BuyPlan:
    action: BuyAction | WearSet | None
    verdict: str
    target: TargetView | None
    candidates: tuple[TargetView, ...]
    money: MoneyView


def _plain(line: str) -> str:
    return line.replace(_VS16, "")


@dataclass(frozen=True)
class _Shop:
    gadgets: GadgetsState
    level: int
    money: MoneyView
    full: bool

    def affords(self, price: int) -> bool:
        return self.money.available is not None and self.money.available >= price

    def copy_in_bag(self, item: ShopItem) -> bool:
        return any(g.code == item.code and shop_of(g) is not None for g in self.gadgets.bag)

    def pick(
        self, slot: ShopSlot, floor: int, prefer: ShopItem | None = None
    ) -> tuple[ShopItem | None, bool]:
        """Лучший тир слота сильнее `floor`, доступный по уровню, по карману или уже в рюкзаке;
        `prefer` (часть цели) — в первую очередь. Второе — был ли тир, на который не хватило."""
        reachable = [
            item
            for item in SHOP[slot]
            if item.required_level <= self.level and base_bonus(item) > floor
        ]
        buyable = [i for i in reachable if self.copy_in_bag(i) or self.can_buy(i)]
        if prefer is not None and prefer in buyable:
            return prefer, False
        best = max(buyable, key=lambda i: i.tier, default=None)
        return best, best is None and bool(reachable)

    def can_buy(self, item: ShopItem) -> bool:
        return not self.full and self.affords(item.price)

    def action(self, rule: Literal["empty", "set", "replace"], item: ShopItem) -> BuyAction:
        wear = rule != "set"
        if rule != "set" and self.copy_in_bag(item):
            return BuyAction(rule, item.slot, item.tier, 0, wear, 0, in_bag=True)
        return BuyAction(
            rule, item.slot, item.tier, item.price, wear, self.sell_needed(item.price)
        )

    def sell_needed(self, price: int) -> int:
        return max(0, price - ((self.money.cash or 0) - self.money.reserve))


def _target_view(key: SetKey, shop: _Shop) -> TargetView | None:
    """Цель из белого списка; None — сет надет и его строка есть в `/inv`."""
    target = SETS[key]
    if target.shop_tier is None:
        return None
    worn_items, bag = shop.gadgets.items, shop.gadgets.bag
    tier = target.shop_tier
    parts = {slot: SHOP[slot][tier - 1] for slot in _SHOP_SLOTS}

    def counted(slot: UpSlot) -> bool:
        return any(up_slot(g) == slot and qualifies(g, target) for g in worn_items)

    def bought(slot: UpSlot) -> bool:
        return any(
            up_slot(g) == slot and (s := set_of(g)) is not None and s.key == key for g in bag
        )

    worn = tuple(slot for slot in _SHOP_SLOTS if counted(slot))
    in_bag = tuple(slot for slot in _SHOP_SLOTS if slot not in worn and bought(slot))
    missing = tuple(
        sorted(
            (
                (slot, tier, parts[slot].price)
                for slot in _SHOP_SLOTS
                if slot not in worn and slot not in in_bag
            ),
            key=lambda m: m[2],
        )
    )
    uncounted = tuple(slot for slot in _EXTRA_SLOTS if not counted(slot))
    blocked_by: tuple[UpSlot, ...] = ()
    need = 0
    status: TargetStatus
    if set_score(worn_items, target) >= SET_MIN_SLOTS:
        if target.line is None:
            status = "unconfirmed"
        elif _plain(target.line) in {_plain(line) for line in shop.gadgets.sets}:
            return None
        else:
            status = "worn_inactive"
    elif any(part.required_level > shop.level for part in parts.values()):
        status = "level"
    elif len(_SHOP_SLOTS) + len(_EXTRA_SLOTS) - len(uncounted) < SET_MIN_SLOTS:
        status, blocked_by = "blocked", uncounted
    elif not missing:
        status = "wearing"
    elif shop.affords(missing[0][2]):
        status = "ready"
    else:
        status = "saving"
        need = max(0, missing[0][2] - (shop.money.available or 0))
    return TargetView(key, status, missing, in_bag, worn, blocked_by, need)


def _locked(gadgets: GadgetsState) -> list[CraftedSet]:
    """Активные крафтовые сеты: строка в `/inv`, а у сета без известной строки — набран по
    предметам."""
    lines = {_plain(line) for line in gadgets.sets}
    return [
        s
        for s in SETS.values()
        if (
            _plain(s.line) in lines
            if s.line is not None
            else set_score(gadgets.items, s) >= SET_MIN_SLOTS
        )
    ]


def _replace(shop: _Shop, settings: Settings) -> tuple[BuyAction | None, bool]:
    """(c): лучший по приросту бонуса тир взамен надетого; при равенстве — дешевле."""
    marks = tuple(_plain(mark) for mark in UPGRADE_SET_MARKS)
    if any(_plain(line).startswith(marks) for line in shop.gadgets.sets):
        return None, False
    task = settings.gadget_upgrade
    busy = task.slot if task.status == "active" else None
    locked = _locked(shop.gadgets)
    best: tuple[int, int, BuyAction] | None = None
    short = False
    for item in shop.gadgets.items:
        slot = up_slot(item)
        info = SLOTS[slot] if slot is not None else None
        if info is None or info.shop is None or slot == busy:
            continue
        if any(qualifies(item, s) for s in locked):
            continue
        current = current_bonus(item)
        choice, lacking = shop.pick(info.shop, current)
        short |= lacking
        if choice is None:
            continue
        action = shop.action("replace", choice)
        gain = base_bonus(choice) - current
        if best is None or (gain, -action.price) > best[:2]:
            best = (gain, -action.price, action)
    return (None if best is None else best[2]), short


def buy_plan(
    state: CharacterState, settings: Settings, reserve: int, own: str | None, now: datetime
) -> BuyPlan:
    money = money_view(state, reserve, own, now)
    known = state.gadgets
    if known is None or known.src == "doubtful" or state.level is None:
        return BuyPlan(None, "unknown", None, (), money)
    used, cap = state.bag, state.bag_cap
    full = used is not None and cap is not None and used.value >= cap.value
    shop = _Shop(known.value, state.level.value, money, full)
    order = sorted(settings.gadgets.sets, key=lambda k: SETS[k].rank, reverse=True)
    candidates = tuple(v for key in order if (v := _target_view(key, shop)) is not None)
    target = next((v for v in candidates if v.status in _ACTIVE), None)

    def plan(action: BuyAction | WearSet | None, verdict: str = "chosen") -> BuyPlan:
        return BuyPlan(action, verdict, target, candidates, money)

    short = False
    taken = {up_slot(g) for g in shop.gadgets.items}
    for slot in _SHOP_SLOTS:
        if slot in taken or (SLOTS[slot].min_level or 1) > shop.level:
            continue
        tier = SETS[target.set].shop_tier if target is not None else None
        prefer = SHOP[slot][tier - 1] if tier is not None else None
        choice, lacking = shop.pick(slot, -1, prefer)
        if choice is not None:
            return plan(shop.action("empty", choice))
        short |= lacking
    if target is not None:
        if target.status == "wearing":
            return plan(WearSet(target.set, target.in_bag))
        if target.status == "ready" and not full:
            slot, tier, _ = target.missing[0]
            return plan(shop.action("set", SHOP[slot][tier - 1]))
        return plan(None, "bag_full" if full else "saving")
    action, lacking = _replace(shop, settings)
    if action is not None:
        return plan(action)
    if full:
        return plan(None, "bag_full")
    if short or lacking:
        return plan(None, "cant_afford")
    if candidates and all(v.status == "blocked" for v in candidates):
        return plan(None, "target_blocked")
    return plan(None, "no_upgrade")


# --- заточка


def upgrade_kind(kind: str, level: int, stocks: Upgrades, white_until: int) -> UpgradeKind | None:
    """Вид улучшения для попытки; None — улучшений нужного вида нет. Авто: ⚪️ до `white_until`,
    дальше 🔴, кончились 🔴 — 🔵."""
    left: dict[UpgradeKind, int] = {"white": stocks.white, "blue": stocks.blue, "red": stocks.red}
    order: tuple[UpgradeKind, ...]
    if kind == "auto":
        order = ("white", "red", "blue") if level < white_until else ("red", "blue")
    else:
        order = tuple(k for k in left if k == kind)
    return next((k for k in order if left[k] > 0), None)


# --- окна-запреты смены снаряжения


def _gorbushka_fight(state: CharacterState, now: datetime) -> tuple[bool, datetime | None] | None:
    """Встреча с продаваном (`meeting`) или ожидание боя (`waiting`) при действующем билете:
    встреча ли это и время боя."""
    seen = state.gorbushka
    if seen is None or seen.value.state not in ("meeting", "waiting"):
        return None
    g = seen.value
    if g.ticket_until is not None and g.ticket_until <= now:
        return None
    return g.state == "meeting", g.next_fight_at


def _battle(state: CharacterState) -> datetime | None:
    seen = state.battle_at
    return None if seen is None else battle_hour(seen.value, seen.at)


def gear_guard(state: CharacterState, now: datetime) -> tuple[str, datetime | None] | None:
    """Почему сейчас нельзя менять навыки (гаджеты, заточка) и до какого момента; None — можно."""
    if metro_inside(state, now) is not None:
        return "in_metro", None
    if (fight := _gorbushka_fight(state, now)) is not None:
        meeting, at = fight
        if meeting or (at is not None and at - GORBUSHKA_GUARD <= now):
            return "gorbushka_meeting", at
    battle = _battle(state)
    if battle is not None and battle - BATTLE_BEFORE <= now < battle + BATTLE_AFTER:
        return "battle_window", battle + BATTLE_AFTER
    return None


def gear_until(state: CharacterState, now: datetime) -> datetime | None:
    """До какого момента смена навыков разрешена: ближайшее начало окна-запрета; `now` — запрет
    уже идёт; None — окон впереди не видно."""
    if gear_guard(state, now) is not None:
        return now
    starts: list[datetime] = []
    if (fight := _gorbushka_fight(state, now)) is not None and fight[1] is not None:
        starts.append(fight[1] - GORBUSHKA_GUARD)
    battle = _battle(state)
    if battle is not None and battle - BATTLE_BEFORE > now:
        starts.append(battle - BATTLE_BEFORE)
    return min(starts, default=None)
