from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from app.engine.events import Event, Unrecognized
from app.engine.gametime import MSK, tasks_day, to_msk
from app.engine.parsing.activities import (
    ActivityCancelled,
    ActivityFinished,
    ActivityStarted,
    BonusRewards,
    DeedsMenu,
    MotivationFull,
    PricesScreen,
    WorkshopScreen,
)
from app.engine.parsing.battle import BattleTargetSet
from app.engine.parsing.bulls import BullsInvite, BullsJoined, BullsRefused, BullsResult
from app.engine.parsing.common import Rewards
from app.engine.parsing.crew import CrewScreen, FactoryReport, FactoryScreen, FactorySignup
from app.engine.parsing.daily import (
    ChosenTask,
    DailyTasksScreen,
    TaskChosen,
    TaskCompleted,
)
from app.engine.parsing.food import FastfoodEaten, FoodMenu
from app.engine.parsing.gorbushka import GorbushkaFight, GorbushkaScreen
from app.engine.parsing.items import (
    BookRead,
    CardUsed,
    ContainerOpened,
    GiftsScreen,
    Inventory,
    PrizeboxOpened,
)
from app.engine.parsing.levelup import LevelUpStep
from app.engine.parsing.lottery import LotteryBought, LotteryCurrency, LotteryScreen
from app.engine.parsing.metro import (
    METRO_COOLDOWN,
    MetroBuffs,
    MetroChest,
    MetroChestOpened,
    MetroEarlyExit,
    MetroEntered,
    MetroEntrance,
    MetroExit,
    MetroFight,
    MetroFinished,
    MetroFirstAid,
    MetroLoot,
    MetroMap,
    MetroNpc,
)
from app.engine.parsing.profile import ProfileCompact
from app.engine.parsing.refusals import Busy, Refused
from app.engine.parsing.screens import (
    BattleMenu,
    BattleReport,
    DeedFinishedInstantly,
    EtherScreen,
    InfoScreen,
    LotterySkillsExpired,
    LotteryWin,
    ResourcesChanged,
)
from app.engine.parsing.sleep import (
    FellAsleep,
    RobberyFight,
    RobberyLoss,
    SleepMenu,
    SleepPlace,
    SleepWarning,
    WokeUp,
)
from app.engine.parsing.smoothie import (
    SmoothieCooked,
    SmoothieRecipe,
    SmoothieScreen,
    recipe_need,
)
from app.engine.parsing.stocks import Dividends, StockBought, StockScreen, StockSold
from app.engine.parsing.swinfo import BattleSummary, FactoryCall, FactoryResult
from app.engine.parsing.tangerine import TangerineRefused
from app.engine.state.ledger import Effect, amounts
from app.engine.state.model import (
    DAY_SCOPED,
    DEED_PRIORS,
    DEFAULT_PRICES,
    ActivityStat,
    BusyState,
    CharacterState,
    ChosenTaskState,
    FoodStockState,
    GorbushkaState,
    LotteryState,
    MetroRunRef,
    Obs,
    PersonalTask,
    PriceState,
    RefusalState,
    Skills,
    SmoothieRecipeState,
    Src,
    StockLimits,
    TargetSet,
    TaskOfferState,
    TeamTask,
    Upgrades,
    dump_state,
    load_state,
)
from app.engine.types import IncomingMessage

OUTCOME_HORIZON = timedelta(days=14)
# Принудительный сон — через 72 ч бодрствования, лечь снова можно через 12 ч после пробуждения.
AWAKE_LIMIT = timedelta(hours=72)
SLEEP_COOLDOWN = timedelta(hours=12)
FASTFOOD_COOLDOWN = timedelta(minutes=30)
GORBUSHKA_FIGHT_GAP = timedelta(hours=1)
# Продажа билетов закрывается за 10 минут до розыгрыша.
LOTTERY_SALE_ENDS = timedelta(minutes=10)
# На экране покупки за валюту номера тиража нет: тот же тираж — если срок продажи по его отсчёту
# совпадает с известным (отсчёт округлён до минуты).
LOTTERY_SAME_DRAW = timedelta(minutes=2)
LOTTERY_CURRENCIES = ("money", "knowledge", "raw", "details")
# Битва за фабрику — в 18:30 MSK: отчёт о ней (/fb) датируется ею.
FACTORY_BATTLE = time(18, 30)
# Ответы с изменением ресурсов → вид эффекта журнала прихода.
_RESULT_KINDS = {"symbol_exchange": "exchange", "tangerine_gift": "tangerine_gift"}
# Бой с биржевиками: итог приходит через ~5 мин после присоединения (медиана 292 с).
BULLS_FIGHT = timedelta(minutes=5)
FOOD_KINDS = ("hotdog", "pizza", "burger", "banana")
METRIC_FIELDS = (
    "level",
    "exp",
    "money",
    "stamina",
    "motivation",
    "knowledge",
    "raw",
    "details",
    "books",
    "glory",
)
STATS_ALPHA = 0.1
_PROFILE_FIELDS = (
    "level",
    "exp",
    "exp_next",
    "money",
    "stamina",
    "knowledge",
    "raw",
    "details",
    "motivation",
    "motivation_max",
    "bag",
    "bag_cap",
)
_REFUSAL_TIMERS = {
    "card_cooldown": "card_ready_at",
    "prizebox_locked": "prizebox_ready_at",
    "fastfood_cooldown": "fastfood_ready_at",
    "metro_cooldown": "metro_ready_at",
}


# Статус задания за день только растёт: экран или строка с меткой не новее сохранённой (в том
# числе той же секунды, доставленные позже) его не понижают.
_TASK_RANK = {"none": 0, "offers": 0, "active": 1, "done": 2}


def _same_team(task: TeamTask, day: date, resource: str) -> bool:
    # Экранное задание с нераспознанным условием ресурса не знает: строка его дополняет.
    return task.day == day and task.status != "none" and task.resource in (resource, "")


def _demotes(current: Obs[Any], value: Any, at: datetime) -> bool:
    known = current.value
    if at > current.at or known.day != value.day:
        return False
    return bool(_TASK_RANK[value.status] < _TASK_RANK[known.status])


class _Patch:
    """Изменения состояния от одного сообщения: `origin` — создание, `at` — правка."""

    def __init__(
        self, state: CharacterState, at: datetime, origin: datetime, msg_id: int = 0
    ) -> None:
        self.state = state
        self.at = at
        self.origin = min(origin, at)
        self.msg_id = msg_id
        self.updates: dict[str, Any] = {}
        # Применённые итоги (ключ → создание сообщения) и горизонт их хранения.
        self.applied: dict[str, datetime] = dict(state.applied)
        self.horizon = max([*self.applied.values(), self.origin]) - OUTCOME_HORIZON
        # Эффекты для журнала прихода: только применённых итогов.
        self.effects: list[Effect] = []

    def effect(
        self,
        kind: str,
        sums: dict[str, int],
        items: Mapping[str, int] | None = None,
        at: datetime | None = None,
        key: str | None = None,
    ) -> None:
        if sums or items:
            self.effects.append(Effect(kind, sums, dict(items or {}), at, key))

    def first(self, key: str) -> bool:
        """Итог, который приходит разными сообщениями (каждый /fb, /battle), — один раз по своему
        ключу, а не по сообщению. Ключ живёт в `applied` 14 дней; дольше его держит журнал прихода
        (тот же ключ у эффекта — постоянный ключ ряда)."""
        if key in self.applied or self.origin < self.horizon:
            return False
        self.applied[key] = self.origin
        return True

    def get(self, name: str) -> Any:
        return self.updates.get(name, getattr(self.state, name))

    def later(self, seconds: int | None) -> datetime | None:
        return self.at + timedelta(seconds=seconds) if seconds is not None else None

    def snap(self, name: str, value: Any, *, src: Src = "screen") -> None:
        current: Obs[Any] | None = self.get(name)
        if current is not None and self.at < current.at:
            return
        if current is not None and name in DAY_SCOPED and _demotes(current, value, self.at):
            return
        self.updates[name] = Obs(value=value, at=self.at, src=src)

    def _increment_mode(
        self, name: str, since: datetime | None = None
    ) -> Literal["skip", "apply", "doubt"]:
        current: Obs[Any] | None = self.get(name)
        if current is None or current.at > self.at:
            return "skip"
        if name in self.updates:
            return "apply"
        # `since` — момент, раньше которого событие произойти не могло: снимок новее
        # него мог событие уже учесть.
        if current.at < self.origin and (since is None or current.at <= since):
            return "apply"
        # Снимок снят между созданием сообщения и его правкой (или в ту же секунду):
        # неизвестно, учёл ли он событие.
        return "doubt"

    def change(
        self, name: str, fn: Callable[[Any], Any], *, since: datetime | None = None
    ) -> None:
        mode = self._increment_mode(name, since)
        if mode == "skip":
            return
        current: Obs[Any] = self.get(name)
        if mode == "doubt":
            # Значение не трогаем, но не доверяем ему.
            self.updates[name] = current.model_copy(update={"src": "doubtful"})
            return
        src: Src = "doubtful" if current.src == "doubtful" else "derived"
        self.updates[name] = Obs(value=fn(current.value), at=self.at, src=src)

    def delta(self, name: str, diff: int, *, since: datetime | None = None) -> None:
        if diff != 0:
            self.change(name, lambda v: v + diff, since=since)

    def doubt(self, name: str) -> None:
        """Событие изменило значение на неизвестную величину: не трогаем, но не доверяем."""
        current: Obs[Any] | None = self.get(name)
        if current is not None and current.at <= self.at:
            self.updates[name] = current.model_copy(update={"src": "doubtful"})

    def price(self, key: str, value: PriceState) -> None:
        prices: dict[str, Obs[PriceState]] = dict(self.get("prices"))
        current = prices.get(key)
        if current is not None and self.at < current.at:
            return
        prices[key] = Obs(value=value, at=self.at)
        self.updates["prices"] = prices

    def rewards(self, r: Rewards) -> None:
        for name in ("exp", "money", "knowledge", "details", "raw"):
            self.delta(name, getattr(r, name))
        if r.stamina is not None:
            self.snap("stamina", r.stamina)
        if r.upgrades_white or r.upgrades_blue or r.upgrades_red:
            self.change(
                "upgrades",
                lambda u: Upgrades(
                    white=u.white + r.upgrades_white,
                    blue=u.blue + r.upgrades_blue,
                    red=u.red + r.upgrades_red,
                ),
            )
        if r.prizebox:
            self.snap("prizebox", True, src="derived")
            self.snap("prizebox_ready_at", None, src="derived")
        self.delta("containers_small", r.containers_small)
        self.delta("containers_medium", r.containers_medium)
        if r.team_task is not None:
            self.team_line(*r.team_task)
        if r.personal_task is not None:
            self.personal_line(*r.personal_task)

    def team_line(self, current: int, goal: int, resource: str) -> None:
        """Строка командного прогресса: к известному заданию того же дня и ресурса — только
        прогресс (у экранного с нераспознанным условием — ещё и ресурс); иначе новое значение без
        дел, `derived` — план перечитает экран, чтобы их узнать (дела с экрана известны, даже
        если подсказка незнакомая)."""
        day = tasks_day(self.at)
        known: Obs[TeamTask] | None = self.get("team_task")
        if known is not None and _same_team(known.value, day, resource):
            task = known.value
            status = "done" if current >= goal else task.status
            update = {"current": current, "goal": goal, "resource": resource, "status": status}
            self.snap("team_task", task.model_copy(update=update), src=known.src)
            return
        status = "done" if current >= goal else "active"
        value = TeamTask(current=current, goal=goal, resource=resource, day=day, status=status)
        self.snap("team_task", value, src="derived")

    def personal_line(self, current: int, goal: int, resource: str) -> None:
        """Строка личного прогресса обновляет только прогресс выбранного задания того же дня и
        ресурса; иначе игнорируется — план сам перечитает экран."""
        known: Obs[PersonalTask] | None = self.get("daily_personal")
        task = known.value if known is not None else None
        if task is None or task.chosen is None or task.day != tasks_day(self.at):
            return
        if task.chosen.resource != resource:
            return
        self.snap("daily_personal", task.model_copy(update={"current": current}))

    def stat(self, activity: str, r: Rewards) -> None:
        stats: dict[str, ActivityStat] = dict(self.get("activity_stats"))
        old = stats.get(activity) or DEED_PRIORS.get(activity) or ActivityStat()
        fields = ("exp", "money", "knowledge", "details", "raw")
        moved = {
            f: getattr(old, f) + STATS_ALPHA * (getattr(r, f) - getattr(old, f)) for f in fields
        }
        stats[activity] = ActivityStat(count=old.count + 1, **moved)
        self.updates["activity_stats"] = stats

    def result(self) -> CharacterState:
        return self.state.model_copy(update=self.updates) if self.updates else self.state


_HANDLERS: dict[type[Event], Callable[[_Patch, Any], None]] = {}


def _on[E: Event](
    cls: type[E],
) -> Callable[[Callable[[_Patch, E], None]], Callable[[_Patch, E], None]]:
    def register(fn: Callable[[_Patch, E], None]) -> Callable[[_Patch, E], None]:
        _HANDLERS[cls] = fn
        return fn

    return register


@_on(ProfileCompact)
def _profile(p: _Patch, e: ProfileCompact) -> None:
    for name in _PROFILE_FIELDS:
        p.snap(name, getattr(e, name))
    if e.tangerines is not None:
        p.snap("tangerines", e.tangerines)
    p.snap(
        "skills", Skills(practice=e.practice, theory=e.theory, cunning=e.cunning, wisdom=e.wisdom)
    )
    p.snap("motivation_next_at", p.later(e.motivation_next_in_s))
    p.snap("battle_at", p.later(e.battle_in_s))
    p.snap("battle_target", e.battle_target)
    # Значок не распознан — своя компания неизвестна, прежняя не держится: игрок мог перейти в
    # другую. Тег пустой — персонаж не в команде.
    p.snap("company", e.company)
    p.snap("team_tag", e.team_tag)
    busy = None
    if e.busy_kind is not None and e.busy_left_s is not None:
        busy = BusyState(activity=e.busy_kind, until=p.at + timedelta(seconds=e.busy_left_s))
    p.snap("busy", busy)
    if e.sleep_in_s is not None:
        deadline = p.at + timedelta(seconds=e.sleep_in_s)
        p.snap("sleep_deadline", deadline)
        p.snap("sleep_allowed_at", deadline - AWAKE_LIMIT + SLEEP_COOLDOWN, src="derived")


@_on(BattleTargetSet)
def _battle_target(p: _Patch, e: BattleTargetSet) -> None:
    p.snap("battle_target", e.target)
    p.snap("battle_at", p.later(e.battle_in_s))
    # Цель сбрасывается после каждой битвы, а защиту профиль не показывает: запоминаем, какая
    # цель выставлена и на какую битву.
    p.snap(
        "battle_target_set",
        TargetSet(target=e.target, battle_at=p.at + timedelta(seconds=e.battle_in_s)),
    )
    if e.zero_stamina:
        p.snap("stamina", 0)


def _motivation_cost(p: _Patch, activity: str) -> int:
    prices: dict[str, Obs[PriceState]] = p.get("prices")
    if (known := prices.get(activity)) is not None:
        return known.value.motivation
    default = DEFAULT_PRICES.get(activity)
    return default.motivation if default is not None else 1


@_on(ActivityStarted)
def _started(p: _Patch, e: ActivityStarted) -> None:
    p.snap("busy", BusyState(activity=e.activity, until=p.at + timedelta(seconds=e.duration_s)))
    p.delta("money", -e.money)
    p.delta("details", -e.details)
    p.delta("motivation", -_motivation_cost(p, e.activity))
    p.effect("deed_start", amounts(money=-e.money, details=-e.details))


@_on(ActivityFinished)
def _finished(p: _Patch, e: ActivityFinished) -> None:
    p.snap("busy", None)
    p.rewards(e.rewards)
    p.delta("motivation", e.motivation_refund)
    p.stat(e.activity, e.rewards)
    p.effect("deed", amounts(e.rewards), e.rewards.items)


@_on(BonusRewards)
def _bonus(p: _Patch, e: BonusRewards) -> None:
    p.rewards(e.rewards)
    p.effect("deed", amounts(e.rewards), e.rewards.items)


@_on(ActivityCancelled)
def _cancelled(p: _Patch, e: ActivityCancelled) -> None:
    if e.result != "ok":
        return
    p.snap("busy", None)
    p.delta("motivation", e.motivation)
    p.delta("money", e.money)
    p.effect("deed_start", amounts(money=e.money))


@_on(MotivationFull)
def _motivation_full(p: _Patch, e: MotivationFull) -> None:
    top: Obs[int] | None = p.get("motivation_max")
    if top is not None:
        p.snap("motivation", top.value, src="derived")
    p.snap("motivation_next_at", None, src="derived")


@_on(Busy)
def _busy(p: _Patch, e: Busy) -> None:
    current: Obs[BusyState | None] | None = p.get("busy")
    activity = "unknown"
    if current is not None and current.value is not None:
        activity = current.value.activity
    p.snap("busy", BusyState(activity=activity, until=p.at + timedelta(seconds=e.left_s)))


@_on(Refused)
def _refused(p: _Patch, e: Refused) -> None:
    p.snap("last_refusal", RefusalState(reason=e.reason, need=e.need))
    if e.reason == "no_motivation":
        p.snap("motivation", 0, src="derived")
    elif e.reason == "levelup_required":
        p.snap("levelup_pending", True)
    elif e.reason in _REFUSAL_TIMERS and e.left_s is not None:
        p.snap(_REFUSAL_TIMERS[e.reason], p.later(e.left_s))


@_on(SleepWarning)
def _sleep_warning(p: _Patch, e: SleepWarning) -> None:
    p.snap("sleep_deadline", p.later(e.forced_in_s))


@_on(FellAsleep)
def _fell_asleep(p: _Patch, e: FellAsleep) -> None:
    p.snap("busy", BusyState(activity=f"sleep_{e.where}", until=p.at + timedelta(hours=e.hours)))
    p.snap("sleep_deadline", None, src="derived")
    p.delta("money", -e.cost)
    p.effect("hotel", amounts(money=-e.cost))


@_on(SleepMenu)
def _sleep_menu(p: _Patch, e: SleepMenu) -> None:
    p.price("hotel", PriceState(money=e.hotel_cost))


@_on(SleepPlace)
def _sleep_place(p: _Patch, e: SleepPlace) -> None:
    p.price("hotel", PriceState(money=e.hotel_cost))


def _woke(p: _Patch, rewards: Rewards) -> None:
    p.snap("busy", None)
    p.snap("woke_at", p.at)
    p.snap("sleep_deadline", p.at + AWAKE_LIMIT, src="derived")
    p.snap("sleep_allowed_at", p.at + SLEEP_COOLDOWN, src="derived")
    p.rewards(rewards)


@_on(WokeUp)
def _woke_up(p: _Patch, e: WokeUp) -> None:
    _woke(p, e.rewards)
    p.effect("sleep", amounts(e.rewards))


@_on(RobberyFight)
def _robbery(p: _Patch, e: RobberyFight) -> None:
    _woke(p, e.rewards)
    p.effect("robbery_fight", amounts(e.rewards))


@_on(RobberyLoss)
def _robbery_loss(p: _Patch, e: RobberyLoss) -> None:
    # Сон продолжается. Без строки потери деньги изменились на неизвестную сумму.
    p.rewards(e.rewards)
    if e.money_lost is None:
        p.doubt("money")
    p.effect("robbery", amounts(e.rewards))


@_on(DeedsMenu)
def _deeds(p: _Patch, e: DeedsMenu) -> None:
    p.snap("stamina", e.stamina)
    if e.sleep_in_s is not None:
        p.snap("sleep_deadline", p.later(e.sleep_in_s))
    if e.sleeping is not None and e.sleeping_left_s is not None:
        until = p.at + timedelta(seconds=e.sleeping_left_s)
        p.snap("busy", BusyState(activity=f"sleep_{e.sleeping}", until=until))


@_on(PricesScreen)
def _prices(p: _Patch, e: PricesScreen) -> None:
    for key, price in e.prices.items():
        p.price(
            key,
            PriceState(
                motivation=price.motivation,
                money=price.money,
                minutes=price.minutes,
                details=price.details,
                white=price.white,
                blue=price.blue,
            ),
        )


@_on(WorkshopScreen)
def _workshop(p: _Patch, e: WorkshopScreen) -> None:
    p.snap("money", e.money)
    p.snap("raw", e.raw)
    p.snap("details", e.details)
    p.snap("upgrades", Upgrades(white=e.upgrades_white, blue=e.upgrades_blue, red=e.upgrades_red))


@_on(FoodMenu)
def _food_menu(p: _Patch, e: FoodMenu) -> None:
    p.snap("stamina", e.stamina)
    stock = {k: FoodStockState(count=v.count, low=v.low, high=v.high) for k, v in e.stock.items()}
    p.snap("food_stock", stock)
    p.snap("fastfood_ready_at", p.later(e.fastfood_in_s or 0))


@_on(FastfoodEaten)
def _fastfood(p: _Patch, e: FastfoodEaten) -> None:
    p.snap("stamina", e.stamina)
    p.snap("fastfood_ready_at", p.at + FASTFOOD_COOLDOWN, src="derived")
    p.delta("motivation", e.motivation)
    stock: Obs[dict[str, FoodStockState]] | None = p.get("food_stock")
    if stock is None or e.food not in stock.value:
        return

    def eaten(left: dict[str, FoodStockState]) -> dict[str, FoodStockState]:
        item = left[e.food]
        return {**left, e.food: item.model_copy(update={"count": max(item.count - 1, 0)})}

    p.change("food_stock", eaten)


@_on(Inventory)
def _inventory(p: _Patch, e: Inventory) -> None:
    for name in ("books", "cards", "bag", "bag_cap", "prizebox"):
        p.snap(name, getattr(e, name))
    p.snap("book_ready_at", p.later(e.books_in_s or 0))
    p.snap("card_ready_at", p.later(e.cards_in_s or 0))
    p.snap("prizebox_ready_at", p.later(e.prizebox_in_s or 0) if e.prizebox else None)


@_on(BookRead)
def _book(p: _Patch, e: BookRead) -> None:
    p.delta("exp", e.exp)
    p.delta("books", -1)
    p.snap("book_ready_at", p.later(e.next_in_s))
    p.effect("book", amounts(exp=e.exp))


@_on(CardUsed)
def _card(p: _Patch, e: CardUsed) -> None:
    p.delta("money", e.money)
    p.delta("cards", -1)
    p.snap("card_ready_at", p.later(e.next_in_s))
    p.effect("card", amounts(money=e.money))


@_on(GiftsScreen)
def _gifts(p: _Patch, e: GiftsScreen) -> None:
    p.snap("containers_small", e.containers_small)
    p.snap("containers_medium", e.containers_medium)
    if e.tangerines is not None:
        p.snap("tangerines", e.tangerines)


@_on(ContainerOpened)
def _container(p: _Patch, e: ContainerOpened) -> None:
    p.delta(f"containers_{e.size}", -1)
    p.rewards(e.rewards)
    p.effect("container", amounts(e.rewards), e.rewards.items)


@_on(PrizeboxOpened)
def _prizebox(p: _Patch, e: PrizeboxOpened) -> None:
    p.snap("prizebox", False)
    p.snap("prizebox_ready_at", None)
    if e.money_after is not None:
        # «Стало: $…» — снимок денег; явная прибавка уже в нём.
        p.rewards(replace(e.rewards, money=0))
        p.snap("money", e.money_after)
    else:
        p.rewards(e.rewards)
    p.effect("prizebox", amounts(e.rewards), e.rewards.items)


@_on(GorbushkaScreen)
def _gorbushka(p: _Patch, e: GorbushkaScreen) -> None:
    previous: Obs[GorbushkaState] | None = p.get("gorbushka")
    # Билет куплен после экрана «нужен билет»: его момент — нижняя граница покупки.
    bought_after = (
        previous.at
        if previous is not None
        and previous.at <= p.at
        and previous.value.state == "need_ticket"
        and e.state in ("meeting", "waiting")
        and e.won == 0
        else None
    )
    next_fight = p.at if e.state == "meeting" else p.later(e.next_in_s)
    p.snap(
        "gorbushka",
        GorbushkaState(
            state=e.state,
            won=e.won,
            total=e.total,
            ticket_until=p.later(e.ticket_left_s),
            next_fight_at=next_fight,
            comeback_at=p.later(e.comeback_in_s),
            fight_cost=e.fight_cost_motivation,
        ),
    )
    for name in ("stamina", "money", "knowledge"):
        if (observed := getattr(e, name)) is not None:
            p.snap(name, observed)
    if e.state == "need_ticket" and e.ticket_money is not None:
        p.price(
            "gorbushka_ticket",
            PriceState(money=e.ticket_money, knowledge=e.ticket_knowledge or 0),
        )
    prices: dict[str, Obs[PriceState]] = p.get("prices")
    ticket = prices.get("gorbushka_ticket")
    if bought_after is not None and ticket is not None:
        p.delta("money", -ticket.value.money, since=bought_after)
        p.delta("knowledge", -ticket.value.knowledge, since=bought_after)
        cost = amounts(money=-ticket.value.money, knowledge=-ticket.value.knowledge)
        p.effect("gorbushka_ticket", cost)


@_on(GorbushkaFight)
def _gorbushka_fight(p: _Patch, e: GorbushkaFight) -> None:
    p.rewards(e.rewards)
    p.effect("gorbushka_fight", amounts(e.rewards))
    current: Obs[GorbushkaState] | None = p.get("gorbushka")
    cost = current.value.fight_cost if current is not None else None
    p.delta("motivation", -(cost if cost is not None else 1))
    if current is None or p.at < current.at:
        return
    won = current.value.won
    if e.won and won is not None:
        won += 1
    p.snap(
        "gorbushka",
        current.value.model_copy(
            update={"state": "waiting", "won": won, "next_fight_at": p.at + GORBUSHKA_FIGHT_GAP}
        ),
        src="derived",
    )


def _add_skills(p: _Patch, changes: dict[str, int]) -> None:
    def apply(skills: Skills) -> Skills:
        update = {name: getattr(skills, name) + n for name, n in changes.items()}
        return skills.model_copy(update=update)

    if changes:
        p.change("skills", apply)


@_on(LevelUpStep)
def _levelup(p: _Patch, e: LevelUpStep) -> None:
    if e.step == "menu":
        p.snap("levelup_pending", True)
    elif e.step == "done":
        p.snap("levelup_pending", False)
        p.delta("money", e.money)
        p.delta("motivation", e.motivation)
        p.effect("levelup", amounts(money=e.money))
    if e.skill is not None and e.skill in Skills.model_fields:
        _add_skills(p, {e.skill: 1})


@_on(BattleMenu)
def _battle_menu(p: _Patch, e: BattleMenu) -> None:
    p.snap("battle_at", p.later(e.battle_in_s))


@_on(CrewScreen)
def _crew(p: _Patch, e: CrewScreen) -> None:
    p.snap("team_tag", e.tag)
    p.snap("factory_wins", e.factory_wins)
    if e.glory is not None:
        p.snap("glory", e.glory)
    if e.signup_open:
        # Запасной сигнал о начале записи, если SWINFO пропущен.
        p.snap("factory_call_at", p.at)


def _chosen(task: ChosenTask) -> ChosenTaskState:
    return ChosenTaskState(
        type=task.type,
        level=task.level,
        goal=task.goal,
        resource=task.resource,
        activities=task.activities,
    )


@_on(DailyTasksScreen)
def _daily_screen(p: _Patch, e: DailyTasksScreen) -> None:
    # Полный снимок обоих заданий за день экрана.
    day = tasks_day(p.at)
    if e.chosen is not None:
        status: Literal["active", "done"] = "done" if e.chosen.done else "active"
        personal = PersonalTask(
            day=day, status=status, chosen=_chosen(e.chosen), current=e.chosen.current
        )
    else:
        offers = tuple(
            TaskOfferState(type=o.type, level=o.level, goal=o.goal, trophies=o.trophies)
            for o in e.offers
        )
        personal = PersonalTask(day=day, status="offers", offers=offers)
    p.snap("daily_personal", personal)
    team = TeamTask(current=0, goal=0, resource="", day=day, status="none")
    if e.team is not None:
        t = e.team
        team = TeamTask(
            current=t.current,
            goal=t.goal,
            resource=t.resource,
            day=day,
            status="done" if t.done or 0 < t.goal <= t.current else "active",
            activities=t.activities,
        )
    p.snap("team_task", team)


@_on(TaskChosen)
def _task_chosen(p: _Patch, e: TaskChosen) -> None:
    task = PersonalTask(day=tasks_day(p.at), status="active", chosen=_chosen(e.chosen))
    p.snap("daily_personal", task)


@_on(TaskCompleted)
def _task_completed(p: _Patch, e: TaskCompleted) -> None:
    p.rewards(e.rewards)
    p.effect("task", amounts(e.rewards, trophies=e.trophies))
    day = tasks_day(p.at)
    known: Obs[PersonalTask] | None = p.get("daily_personal")
    if known is not None and known.value.day == day:
        task = known.value
        goal = task.chosen.goal if task.chosen is not None else task.current
        done = task.model_copy(update={"status": "done", "current": goal})
    else:
        # Деталей нет, но и этого достаточно, чтобы задание не брать и не выполнять.
        done = PersonalTask(day=day, status="done")
    p.snap("daily_personal", done, src="derived")


@_on(FactoryResult)
def _factory_result(p: _Patch, e: FactoryResult) -> None:
    # Победа своей команды — следующую битву за фабрику команда пропускает.
    tag: Obs[str | None] | None = p.get("team_tag")
    if tag is not None and e.winner == tag.value:
        p.snap("factory_won_at", p.at)


@_on(FactoryScreen)
def _factory_screen(p: _Patch, e: FactoryScreen) -> None:
    if e.status in ("signed", "not_signed"):
        p.snap("factory_signed", e.status == "signed")


@_on(FactorySignup)
def _factory_signup(p: _Patch, e: FactorySignup) -> None:
    if e.result == "skip":
        p.snap("factory_skip", True)
    else:
        p.snap("factory_signed", True)


@_on(FactoryCall)
def _factory_call(p: _Patch, e: FactoryCall) -> None:
    p.snap("factory_call_at", p.at)


@_on(FactoryReport)
def _factory_report(p: _Patch, e: FactoryReport) -> None:
    # /fb отдаёт последнюю битву с участием персонажа: день только растёт — и не дальше сегодня
    # (иначе дата из будущего залипла бы и отключила запрос сегодняшнего отчёта).
    known: Obs[date] | None = p.get("factory_report_day")
    fresh = known is None or known.value <= e.battle_day
    if fresh and e.battle_day <= tasks_day(p.at):
        p.snap("factory_report_day", e.battle_day)
    key = f"factory:{e.day}"
    if p.first(key):
        battle = datetime.combine(e.battle_day, FACTORY_BATTLE, tzinfo=MSK)
        p.effect("factory", amounts(e.rewards), at=battle.astimezone(UTC), key=key)


def battle_moment(hour: int, seen: datetime) -> datetime:
    """Битва в `hour` часов по Москве — последняя не позже `seen` (отчёт запрошен после неё)."""
    local = to_msk(seen)
    battle = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if battle > local:
        battle -= timedelta(days=1)
    return battle.astimezone(UTC)


@_on(BattleReport)
def _battle_report(p: _Patch, e: BattleReport) -> None:
    # Даты в отчёте нет, а /battle отдаёт последнюю битву с участием — бывает и трёхдневной
    # давности: битва узнаётся по тексту отчёта, а не по вычисленному часу.
    key = f"battle:{e.digest}"
    if p.first(key):
        p.effect("battle", amounts(e.rewards), at=battle_moment(e.hour, p.origin), key=key)


@_on(BullsInvite)
def _bulls_invite(p: _Patch, e: BullsInvite) -> None:
    p.snap("bulls_invite", e.code)


@_on(BullsJoined)
def _bulls_joined(p: _Patch, e: BullsJoined) -> None:
    p.snap("busy", BusyState(activity="bulls", until=p.at + BULLS_FIGHT), src="derived")


@_on(BullsResult)
def _bulls_result(p: _Patch, e: BullsResult) -> None:
    busy: Obs[BusyState | None] | None = p.get("busy")
    # Снимаем только свою занятость: итог мог прийти, пока игрок уже занят другим делом.
    if busy is not None and busy.value is not None and busy.value.activity == "bulls":
        p.snap("busy", None)
    p.rewards(e.rewards)
    p.effect("bulls", amounts(e.rewards))
    if e.won:
        p.snap("bulls_won_at", p.at)


@_on(BullsRefused)
def _bulls_refused(p: _Patch, e: BullsRefused) -> None:
    if e.reason == "already_won":
        p.snap("bulls_won_at", p.at)


def _merged(p: _Patch, name: str, update: dict[str, int]) -> dict[str, int]:
    current: Obs[dict[str, int]] | None = p.get(name)
    return {**(current.value if current is not None else {}), **update}


def _limits(e: StockScreen) -> StockLimits | None:
    # Все лимиты есть только на главном экране биржи.
    if e.min_buy is None or e.max_sell is None or e.reserve is None:
        return None
    if e.open_hour is None or e.close_hour is None:
        return None
    return StockLimits(
        min_buy=e.min_buy,
        max_sell=e.max_sell,
        reserve=e.reserve,
        open_hour=e.open_hour,
        close_hour=e.close_hour,
    )


@_on(StockScreen)
def _stock_screen(p: _Patch, e: StockScreen) -> None:
    if e.quotes:
        p.snap("stock_quotes", e.quotes)
        p.snap("stock_holdings", e.holdings)
    if (limits := _limits(e)) is not None:
        p.snap("stock_limits", limits)
    if e.money is not None:
        p.snap("money", e.money)


def _stock_trade(p: _Patch, e: StockBought | StockSold) -> None:
    p.snap("money", e.money)
    portfolio: Obs[dict[str, int]] | None = p.get("stock_holdings")
    # Портфель неизвестен целиком — не выдумываем его из одной купленной позиции.
    if portfolio is not None:
        p.snap("stock_holdings", {**portfolio.value, e.company: e.shares})


_on(StockBought)(_stock_trade)
_on(StockSold)(_stock_trade)


@_on(Dividends)
def _dividends(p: _Patch, e: Dividends) -> None:
    p.delta("money", e.amount)
    p.effect("dividends", amounts(money=e.amount))


@_on(BattleSummary)
def _battle_summary(p: _Patch, e: BattleSummary) -> None:
    if e.prices:
        p.snap("stock_quotes", _merged(p, "stock_quotes", e.prices))


@_on(SmoothieScreen)
def _smoothie_screen(p: _Patch, e: SmoothieScreen) -> None:
    p.snap("smoothie_ingredients", e.ingredients)
    p.snap("smoothie_bonus", e.bonus)


@_on(SmoothieCooked)
def _smoothie_cooked(p: _Patch, e: SmoothieCooked) -> None:
    p.snap("smoothie_bonus", e.bonus)
    used = recipe_need(e.recipe)

    def spent(ingredients: dict[str, int]) -> dict[str, int]:
        return {k: v - used.get(k, 0) for k, v in ingredients.items()}

    p.change("smoothie_ingredients", spent)


@_on(SmoothieRecipe)
def _smoothie_recipe(p: _Patch, e: SmoothieRecipe) -> None:
    p.snap("smoothie_recipe", SmoothieRecipeState(recipe=e.recipe, bonus=e.bonus))


@_on(TangerineRefused)
def _tangerine(p: _Patch, e: TangerineRefused) -> None:
    if e.reason == "cooldown" and e.left_s is not None:
        p.snap("tangerine_ready_at", p.later(e.left_s))
    elif e.reason == "not_player" and e.target is not None:
        p.snap("tangerine_not_player", e.target)


@_on(MetroEntrance)
def _metro_entrance(p: _Patch, e: MetroEntrance) -> None:
    # Экран входа вместо отказа — кулдаун прошёл.
    p.snap("metro_ready_at", p.at)
    if e.motivation < e.cost:
        # Планировщик считал, что 🔥 на вход хватит: без снимка он заходил бы снова.
        p.snap("motivation", e.motivation)


@_on(Unrecognized)
def _unrecognized(p: _Patch, e: Unrecognized) -> None:
    # Незнакомый экран забега: автопродолжение ждёт нового распознанного экрана (или человека).
    inside: Obs[MetroRunRef | None] | None = p.get("metro_message")
    if inside is not None and inside.value is not None and inside.value.message_id == p.msg_id:
        p.snap("metro_message", inside.value, src="doubtful")


@_on(MetroEntered)
def _metro_entered(p: _Patch, e: MetroEntered) -> None:
    p.delta("motivation", -e.cost)


def _inside(p: _Patch) -> None:
    # Персонаж в метро: какое сообщение — экран забега (для продолжения после рестарта).
    inside: Obs[MetroRunRef | None] | None = p.get("metro_message")
    run = inside.value if inside is not None else None
    if run is None or run.message_id != p.msg_id:
        run = MetroRunRef(message_id=p.msg_id, battle_at=p.get("battle_at"))
    p.snap("metro_message", run)


def _metro_screen(p: _Patch, e: Event) -> None:
    _inside(p)
    if isinstance(e, MetroMap | MetroFirstAid):
        # 🔋 в метро — настоящая выносливость персонажа.
        p.snap("stamina", e.stamina)
    elif isinstance(e, MetroFight) and e.stamina is not None:
        # Награды боя идут в копилку забега и начисляются только на выходе.
        p.snap("stamina", e.stamina)
    elif isinstance(e, MetroChestOpened) and e.result == "arrow":
        p.snap("stamina", 0)


for _screen in (
    MetroBuffs,
    MetroMap,
    MetroFirstAid,
    MetroFight,
    MetroChestOpened,
    MetroLoot,
    MetroNpc,
    MetroChest,
    MetroEarlyExit,
    MetroExit,
):
    _on(_screen)(_metro_screen)


@_on(MetroFinished)
def _metro_finished(p: _Patch, e: MetroFinished) -> None:
    loot = e.loot
    found_rewards = Rewards(
        exp=loot.get("exp", 0),
        money=loot.get("money", 0),
        knowledge=loot.get("knowledge", 0),
        details=loot.get("details", 0),
        raw=loot.get("raw", 0),
        stamina=e.stamina,
        upgrades_white=loot.get("upgrades_white", 0),
        upgrades_blue=loot.get("upgrades_blue", 0),
        upgrades_red=loot.get("upgrades_red", 0),
    )
    p.rewards(found_rewards)
    food = {kind: n for kind in FOOD_KINDS if (n := loot.get(kind, 0))}
    stock: Obs[dict[str, FoodStockState]] | None = p.get("food_stock")
    if food and stock is not None and all(kind in stock.value for kind in food):

        def found(left: dict[str, FoodStockState]) -> dict[str, FoodStockState]:
            return {
                kind: item.model_copy(update={"count": item.count + food.get(kind, 0)})
                for kind, item in left.items()
            }

        p.change("food_stock", found)
    p.snap("metro_ready_at", p.at + METRO_COOLDOWN, src="derived")
    p.snap("metro_message", None)
    p.effect("metro", amounts(found_rewards))


def _sale_ends(p: _Patch, draw_in_s: int) -> datetime:
    return p.at + timedelta(seconds=draw_in_s) - LOTTERY_SALE_ENDS


def _seen_value(p: _Patch, name: str) -> int | None:
    obs: Obs[int] | None = p.get(name)
    return obs.value if obs is not None and obs.src != "doubtful" else None


@_on(LotteryScreen)
def _lottery_screen(p: _Patch, e: LotteryScreen) -> None:
    known: Obs[LotteryState] | None = p.get("lottery")
    short = known.value.short if known is not None and known.value.draw == e.draw else {}
    state = LotteryState(
        draw=e.draw,
        until=_sale_ends(p, e.draw_in_s),
        bought=dict(e.bought),
        limits=dict(e.limits),
        prices=dict(e.prices),
        short=short,
    )
    p.snap("lottery", state)
    for name, value in e.resources.items():
        p.snap(name, value)


@_on(LotteryBought)
def _lottery_bought(p: _Patch, e: LotteryBought) -> None:
    """«Купить все» — приращение к снимку того же тиража и трата по его ценам; валюты ниже лимита
    после покупки — нехватка (игра покупает сколько может)."""
    known: Obs[LotteryState] | None = p.get("lottery")
    spent = [c for c, n in e.bought.items() if n]
    if known is not None and known.value.draw == e.draw and known.value.prices is not None:
        # Трата известна по ценам снимка тиража, даже если сам снимок сомнителен.
        prices = known.value.prices
        cost = {c: -e.bought[c] * prices[c] for c in spent if c in prices}
        p.effect("lottery_tickets", amounts(**cost))
    if known is None or known.value.draw != e.draw or known.value.bought is None:
        # Другой тираж: прежний снимок не про него, сколько куплено всего — неизвестно.
        p.snap(
            "lottery", LotteryState(draw=e.draw, until=_sale_ends(p, e.draw_in_s)), src="derived"
        )
        for name in spent:
            p.doubt(name)
        return
    mode = p._increment_mode("lottery")
    if mode == "skip":
        return
    if mode == "doubt":
        p.updates["lottery"] = known.model_copy(update={"src": "doubtful"})
        for name in spent:
            p.doubt(name)
        return
    snap = known.value
    prices = snap.prices or {}
    for name in spent:
        if name in prices:
            p.delta(name, -e.bought[name] * prices[name])
        else:
            p.doubt(name)
    bought = {c: (snap.bought or {}).get(c, 0) + e.bought.get(c, 0) for c in LOTTERY_CURRENCIES}
    limits = snap.limits or {}
    short = {
        c: _seen_value(p, c) for c in LOTTERY_CURRENCIES if c in limits and bought[c] < limits[c]
    }
    src: Src = "doubtful" if known.src == "doubtful" else "derived"
    value = snap.model_copy(update={"bought": bought, "short": short})
    p.updates["lottery"] = Obs(value=value, at=p.at, src=src)


@_on(LotteryCurrency)
def _lottery_currency(p: _Patch, e: LotteryCurrency) -> None:
    known: Obs[LotteryState] | None = p.get("lottery")
    if known is None or known.value.bought is None or p.at < known.at:
        return
    snap = known.value
    if abs(_sale_ends(p, e.draw_in_s) - snap.until) > LOTTERY_SAME_DRAW:
        return
    before = (snap.bought or {}).get(e.currency)
    if before is not None and e.bought > before:
        # Правка после клика по количеству: куплено больше известного — списываем разницу.
        p.delta(e.currency, -(e.bought - before) * e.price)
        p.effect("lottery_tickets", amounts(**{e.currency: -(e.bought - before) * e.price}))
    short = dict(snap.short)
    if e.short:
        short[e.currency] = _seen_value(p, e.currency)
    else:
        short.pop(e.currency, None)
    update = {
        "bought": {**(snap.bought or {}), e.currency: e.bought},
        "limits": {**(snap.limits or {}), e.currency: e.limit},
        "prices": {**(snap.prices or {}), e.currency: e.price},
        "short": short,
    }
    p.snap("lottery", snap.model_copy(update=update), src=known.src)


@_on(ResourcesChanged)
def _resources(p: _Patch, e: ResourcesChanged) -> None:
    p.rewards(e.rewards)
    p.effect(_RESULT_KINDS.get(e.source, e.source), amounts(e.rewards), e.rewards.items)


@_on(LotteryWin)
def _lottery_win(p: _Patch, e: LotteryWin) -> None:
    p.rewards(e.rewards)
    p.delta("motivation", e.motivation)
    p.delta("containers_small", e.containers_small)
    p.delta("containers_medium", e.containers_medium)
    _add_skills(p, e.skills)
    extra = {"containers_small": e.containers_small, "containers_medium": e.containers_medium}
    p.effect("lottery_win", amounts(e.rewards, **extra))


@_on(LotterySkillsExpired)
def _lottery_skills_expired(p: _Patch, e: LotterySkillsExpired) -> None:
    p.rewards(e.rewards)
    _add_skills(p, {s: -n for s, n in Counter(e.skills).items()})
    p.effect("lottery_skills", amounts(e.rewards))


@_on(EtherScreen)
def _ether(p: _Patch, e: EtherScreen) -> None:
    p.snap("money", e.money)


@_on(DeedFinishedInstantly)
def _instant(p: _Patch, e: DeedFinishedInstantly) -> None:
    p.snap("busy", None)


@_on(InfoScreen)
def _info_screen(p: _Patch, e: InfoScreen) -> None:
    if e.money is not None:
        p.snap("money", e.money)


class StateReducer:
    def __init__(self) -> None:
        # Конвейер передаёт обратно тот же словарь, что вернул apply: не разбираем его заново.
        self._cache: tuple[dict[str, Any], CharacterState] | None = None

    def _load(self, state: dict[str, Any]) -> CharacterState:
        if self._cache is not None and self._cache[0] is state:
            return self._cache[1]
        return load_state(state)

    def apply(
        self, state: dict[str, Any], msg: IncomingMessage, events: Sequence[Event]
    ) -> dict[str, Any]:
        return self.reduce(state, msg, events)[0]

    def reduce(
        self, state: dict[str, Any], msg: IncomingMessage, events: Sequence[Event]
    ) -> tuple[dict[str, Any], tuple[Effect, ...]]:
        """Новое состояние и эффекты для журнала прихода: эффект есть ровно у применённого
        итога (ключ `applied`, прирост счётчика лотереи, доказанная покупка билета Горбушки)."""
        current = self._load(state)
        patch = _Patch(current, msg.date, msg.origin, msg.msg_id)
        applied, horizon = patch.applied, patch.horizon
        for event in events:
            handler = _HANDLERS.get(type(event))
            if handler is None:
                continue
            if event.outcome:
                key = f"{msg.chat_id}:{msg.msg_id}:{event.kind}"
                if key in applied or patch.origin < horizon:
                    continue
                applied[key] = patch.origin
            handler(patch, event)
        kept = {k: t for k, t in applied.items() if t >= horizon}
        if kept != current.applied:
            patch.updates["applied"] = kept
        result = patch.result()
        if result == current:
            return state, tuple(patch.effects)
        dumped = dump_state(result)
        self._cache = (dumped, result)
        return dumped, tuple(patch.effects)

    def metrics(self, old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, float]:
        out: dict[str, float] = {}
        for name in METRIC_FIELDS:
            before, after = old.get(name), new.get(name)
            if after is None or after.get("value") is None:
                continue
            if before is None or before.get("value") != after["value"]:
                out[name] = float(after["value"])
        return out
