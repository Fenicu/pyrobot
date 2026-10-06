"""Гаджеты: деньги на покупку с акциями чужих компаний, правило крафтового сета, план покупки, вид
заточки и окна-запреты смены снаряжения — чистые функции над состоянием и настройками; задача
заточки (`GadgetRuns`) и уведомления по итогам сценариев гаджетов."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from app.engine.artifact import ENGINE_BY
from app.engine.clock import Clock
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
    set_part,
    shop_item,
    slot_of_icon,
)
from app.engine.notify import NotifierPort
from app.engine.planner.base import BATTLE_AFTER, BATTLE_BEFORE, battle_hour
from app.engine.planner.obligations import DUMP_SPAN, TARGET_LAST_CALL, metro_inside
from app.engine.settings import (
    GadgetUpgradeSection,
    Settings,
    SettingsProvider,
    UpgradeChoice,
    UpgradeStatus,
    UpSlotKey,
)
from app.engine.state.model import CharacterState, GadgetsState, GadgetState, Obs, Upgrades

if TYPE_CHECKING:
    from app.engine.scenarios.library import ScenarioResult

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
    return set_part(item.slot, item.name, item.code)


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
    продать только что купленное; слив выключен — окна нет."""
    seen = state.battle_at
    if seen is None or not settings.features.stocks_dump:
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


_MARKS = tuple(_plain(mark) for mark in UPGRADE_SET_MARKS)
_KNOWN_LINES = frozenset(_plain(s.line) for s in SETS.values() if s.line is not None)


def _upgrade_set(gadgets: GadgetsState) -> bool:
    return any(_plain(line).startswith(_MARKS) for line in gadgets.sets)


def _unknown_set(gadgets: GadgetsState) -> bool:
    """Строка сета не из каталога и не сета заточки: из каких слотов он собран, неизвестно."""
    lines = (_plain(line) for line in gadgets.sets)
    return any(line not in _KNOWN_LINES and not line.startswith(_MARKS) for line in lines)


def _replace(shop: _Shop, settings: Settings) -> tuple[BuyAction | None, bool]:
    """(c): лучший по приросту бонуса тир взамен надетого; при равенстве — дешевле."""
    if _upgrade_set(shop.gadgets) or _unknown_set(shop.gadgets):
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
    # Неулучшенный гаджет на пустом слоте снял бы сет заточки.
    empty = () if _upgrade_set(shop.gadgets) else _SHOP_SLOTS
    for slot in empty:
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


# --- задача заточки


class GadgetConflict(Exception):
    """Переход задачи заточки сейчас невозможен; `code` — код ответа API: `upgrade_in_progress`,
    `not_worn`, `target_reached`, `no_task`, `tg_not_online`, `dry_run`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def worn_on(state: CharacterState, slot: str) -> GadgetState | None:
    seen = state.gadgets
    if seen is None:
        return None
    return next((g for g in seen.value.items if up_slot(g) == slot), None)


def _task_gadget(task: GadgetUpgradeSection, state: CharacterState) -> GadgetState | None:
    """Гаджет задачи на её слоте; на слоте другой или пусто — None."""
    item = None if task.slot is None else worn_on(state, task.slot)
    return item if item is not None and item.name == task.gadget else None


def _with_task(settings: Settings, task: GadgetUpgradeSection) -> Settings:
    return settings.model_copy(update={"gadget_upgrade": task})


def _active(settings: Settings) -> GadgetUpgradeSection:
    task = settings.gadget_upgrade
    if task.status != "active":
        raise GadgetConflict("no_task")
    return task


def start_upgrade(
    settings: Settings,
    state: CharacterState,
    slot: UpSlotKey,
    target: int,
    kind: UpgradeChoice,
    now: datetime,
) -> Settings:
    task = settings.gadget_upgrade
    if task.status == "active":
        raise GadgetConflict("upgrade_in_progress")
    item = worn_on(state, slot)
    if item is None:
        raise GadgetConflict("not_worn")
    level = item.level or 0
    if target <= level:
        raise GadgetConflict("target_reached")
    started = GadgetUpgradeSection(
        status="active",
        task_id=task.task_id + 1,
        slot=slot,
        gadget=item.name,
        kind=kind,
        target=target,
        start_level=level,
        started_at=now,
    )
    return _with_task(settings, started)


def end_upgrade(
    settings: Settings, status: UpgradeStatus, reason: str, level: int | None, now: datetime
) -> Settings:
    update = {"status": status, "end_reason": reason, "end_level": level, "ended_at": now}
    return _with_task(settings, _active(settings).model_copy(update=update))


def stop_upgrade(settings: Settings, now: datetime) -> Settings:
    return end_upgrade(settings, "stopped", "stopped", None, now)


@dataclass(frozen=True)
class UpgradeTaskView:
    """Задача заточки для API: поля записи и текущий уровень гаджета задачи по состоянию."""

    status: UpgradeStatus
    task_id: int
    slot: UpSlotKey | None
    gadget: str | None
    kind: UpgradeChoice | None
    target: int | None
    start_level: int | None
    end_level: int | None
    started_at: datetime | None
    ended_at: datetime | None
    end_reason: str | None
    level: int | None


def task_view(settings: Settings, state: CharacterState) -> UpgradeTaskView:
    task = settings.gadget_upgrade
    item = _task_gadget(task, state)
    return UpgradeTaskView(
        status=task.status,
        task_id=task.task_id,
        slot=task.slot,
        gadget=task.gadget,
        kind=task.kind,
        target=task.target,
        start_level=task.start_level,
        end_level=task.end_level,
        started_at=task.started_at,
        ended_at=task.ended_at,
        end_reason=task.end_reason,
        level=None if item is None else item.level or 0,
    )


def upgrades_exhausted(
    task: GadgetUpgradeSection, state: CharacterState, white_until: int
) -> bool:
    """Улучшений вида задачи нет по экрану `/upgrades`, снятому после старта; производные запасы
    (после попыток), экраны `/up_` и снимки до старта не в счёт."""
    info, stocks = state.upgrade_info, state.upgrades
    item = _task_gadget(task, state)
    if info is None or info.src == "doubtful" or stocks is None or stocks.src != "screen":
        return False
    if task.started_at is None or info.at < task.started_at or stocks.at != info.at:
        return False
    level = (item.level or 0) if item is not None else (task.start_level or 0)
    return upgrade_kind(task.kind or "auto", level, stocks.value, white_until) is None


def _bag_full(state: CharacterState) -> bool | None:
    used, cap = state.bag, state.bag_cap
    if used is None or cap is None or "doubtful" in (used.src, cap.src):
        return None
    return used.value >= cap.value


# Итоги порции, закрывающие задачу; прочие (конец порции, занят, пауза) её не трогают.
_UPGRADE_RESULTS = {
    ("done", "target_reached"),
    ("done", "exhausted"),
    ("nothing", "gadget_changed"),
}
_UPGRADE_ENDS: dict[str, tuple[UpgradeStatus, Literal["info", "warn"], str]] = {
    "target_reached": ("done", "info", "gadget_upgrade_done"),
    "exhausted": ("exhausted", "warn", "gadget_upgrade_exhausted"),
    "gadget_changed": ("failed", "warn", "gadget_upgrade_failed"),
}


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class GadgetRuns:
    """Задача заточки в движке аккаунта: переходы записи `gadget_upgrade` через настройки движка
    (атомарно с другими правками, как `ArtifactRuns`) и уведомления по итогам сценариев
    гаджетов."""

    def __init__(
        self,
        *,
        settings: SettingsProvider,
        state: Callable[[], CharacterState],
        notifier: NotifierPort,
        clock: Clock,
    ) -> None:
        self._settings = settings
        self._state = state
        self._notifier = notifier
        self._clock = clock
        # О полном рюкзаке уже сказали: снова — после `bag < bag_cap`.
        self._bag_told = False

    def view(self) -> UpgradeTaskView:
        return task_view(self._settings.current, self._state())

    async def _apply(self, change: Callable[[Settings], Settings], by: str) -> Settings:
        new, _ = await self._settings.update(change, changed_by=by)
        return new

    async def start(self, slot: UpSlotKey, target: int, kind: UpgradeChoice, *, by: str) -> None:
        now, state = self._clock.now(), self._state()
        await self._apply(lambda s: start_upgrade(s, state, slot, target, kind, now), by)

    async def stop(self, *, by: str) -> None:
        now = self._clock.now()
        await self._apply(lambda s: stop_upgrade(s, now), by)

    async def _end(
        self, task_id: int, status: UpgradeStatus, reason: str, level: int | None
    ) -> GadgetUpgradeSection | None:
        """Переход движка по задаче `task_id`: её уже закрыли или запустили новую — ничего."""
        now = self._clock.now()

        def change(s: Settings) -> Settings:
            if s.gadget_upgrade.task_id != task_id:
                raise GadgetConflict("no_task")
            return end_upgrade(s, status, reason, level, now)

        try:
            new = await self._apply(change, ENGINE_BY)
        except GadgetConflict:
            return None
        return new.gadget_upgrade

    async def _ended(self, task_id: int, reason: str, level: int | None) -> None:
        status, severity, code = _UPGRADE_ENDS[reason]
        task = await self._end(task_id, status, reason, level)
        if task is None:
            return
        shown = "?" if level is None else level
        where = f"{task.gadget} ({task.slot})"
        texts = {
            "target_reached": f"{where}: level {shown}, target {task.target} reached",
            "exhausted": f"{where}: no {task.kind} upgrades left at level {shown}, "
            f"target {task.target}",
            "gadget_changed": f"{task.slot}: {task.gadget} is no longer worn; upgrade stopped",
        }
        await self._notifier.notify(severity, code, texts[reason])

    async def after(
        self, scenario: str, params: Mapping[str, Any], result: ScenarioResult
    ) -> None:
        """Итог сценария гаджетов: конец задачи заточки и уведомления покупки и надевания."""
        details = result.details or {}
        if scenario == "gadget_upgrade":
            await self._upgrade_result(params, result, details)
        elif scenario == "gadget_buy":
            await self._buy_result(result, details)
        elif scenario == "gadget_wear_set" and result.status == "done":
            await self._wear_result(details)

    async def _upgrade_result(
        self, params: Mapping[str, Any], result: ScenarioResult, details: Mapping[str, Any]
    ) -> None:
        if (result.status, result.reason) not in _UPGRADE_RESULTS:
            return
        task_id = _int(details.get("task_id", params.get("task_id")))
        task = self._settings.current.gadget_upgrade
        if task_id is None or task_id != task.task_id or task.status != "active":
            return
        await self._ended(task_id, result.reason, _int(details.get("level")))

    async def _buy_result(self, result: ScenarioResult, d: Mapping[str, Any]) -> None:
        # `done worn` — надет экземпляр из рюкзака, без покупки: не о чем сообщать.
        if (result.status, result.reason) == ("done", "bought"):
            text = f"bought {d.get('bought')} for ${d.get('price')} ({d.get('rule')})"
            sold = sum(_int(s.get("n")) or 0 for s in d.get("sold") or ())
            if sold:
                text += f", sold {sold} shares"
            await self._notifier.notify("info", "gadget_bought", text)
        elif (result.status, result.reason) == ("nothing", "shop_mismatch"):
            seen = d.get("seen") or {}
            shown = f"{seen.get('name')} ${seen.get('price')} level {seen.get('level')}"
            text = f"shop {d.get('slot')} tier {d.get('tier')} differs from catalog: {shown}"
            await self._notifier.notify("warn", "gadget_shop_mismatch", text)

    async def _wear_result(self, d: Mapping[str, Any]) -> None:
        name, active = d.get("set"), d.get("active")
        if active is True:
            await self._notifier.notify("info", "gadget_set_worn", f"set {name} worn and active")
        elif active is False:
            await self._notifier.notify(
                "warn", "gadget_set_inactive", f"set {name} worn but its line is not in /inv"
            )
        else:
            before = ", ".join(d.get("sets_before") or ()) or "-"
            after = ", ".join(d.get("sets") or ()) or "-"
            await self._notifier.notify(
                "info",
                "gadget_set_unconfirmed",
                f"set {name} worn; set lines before: {before}; after: {after}",
            )

    async def tick(self) -> None:
        """Полный рюкзак при покупке гаджетов и сверка задачи заточки по состоянию: цель
        достигнута вручную, на слоте другой гаджет, улучшения кончились."""
        settings, state = self._settings.current, self._state()
        await self._bag_check(settings, state)
        task = settings.gadget_upgrade
        if task.status != "active" or task.slot is None:
            return
        item = _task_gadget(task, state)
        seen = state.gadgets
        if item is not None and task.target is not None and (item.level or 0) >= task.target:
            await self._ended(task.task_id, "target_reached", item.level or 0)
        elif item is None and seen is not None and seen.src != "doubtful":
            await self._ended(task.task_id, "gadget_changed", None)
        elif upgrades_exhausted(task, state, settings.gadgets.white_until):
            level = (item.level or 0) if item is not None else task.start_level
            await self._ended(task.task_id, "exhausted", level)

    async def _bag_check(self, settings: Settings, state: CharacterState) -> None:
        full = _bag_full(state)
        if full is False:
            self._bag_told = False
        if not full or not settings.features.gadgets_buy or self._bag_told:
            return
        self._bag_told = True
        used, cap = state.bag, state.bag_cap
        assert used is not None and cap is not None
        await self._notifier.notify(
            "warn",
            "gadget_bag_full",
            f"bag full: {used.value} of {cap.value}; gadget purchase paused",
        )
