from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Literal

from app.engine.events import Event
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
from app.engine.parsing.common import Rewards
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
from app.engine.parsing.profile import ProfileCompact
from app.engine.parsing.refusals import Busy, Refused
from app.engine.parsing.sleep import FellAsleep, RobberyFight, SleepMenu, SleepWarning, WokeUp
from app.engine.state.model import (
    BusyState,
    CharacterState,
    FoodStockState,
    GorbushkaState,
    Obs,
    PriceState,
    RefusalState,
    Skills,
    Src,
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
)
DEFAULT_MOTIVATION_COST = {"harvest": 1, "job": 1, "learn": 2, "dconv": 1, "eat": 0}
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
}


class _Patch:
    """Изменения состояния от одного сообщения: `origin` — создание, `at` — правка."""

    def __init__(self, state: CharacterState, at: datetime, origin: datetime) -> None:
        self.state = state
        self.at = at
        self.origin = min(origin, at)
        self.updates: dict[str, Any] = {}

    def get(self, name: str) -> Any:
        return self.updates.get(name, getattr(self.state, name))

    def later(self, seconds: int | None) -> datetime | None:
        return self.at + timedelta(seconds=seconds) if seconds is not None else None

    def snap(self, name: str, value: Any, *, src: Src = "screen") -> None:
        current: Obs[Any] | None = self.get(name)
        if current is not None and self.at < current.at:
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
        if r.team_task is not None:
            current, goal, resource = r.team_task
            self.snap("team_task", TeamTask(current=current, goal=goal, resource=resource))

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
    if e.zero_stamina:
        p.snap("stamina", 0)


def _motivation_cost(p: _Patch, activity: str) -> int:
    prices: dict[str, Obs[PriceState]] = p.get("prices")
    known = prices.get(activity)
    if known is not None:
        return known.value.motivation
    return DEFAULT_MOTIVATION_COST.get(activity, 1)


@_on(ActivityStarted)
def _started(p: _Patch, e: ActivityStarted) -> None:
    p.snap("busy", BusyState(activity=e.activity, until=p.at + timedelta(seconds=e.duration_s)))
    p.delta("money", -e.money)
    p.delta("details", -e.details)
    p.delta("motivation", -_motivation_cost(p, e.activity))


@_on(ActivityFinished)
def _finished(p: _Patch, e: ActivityFinished) -> None:
    p.snap("busy", None)
    p.rewards(e.rewards)
    p.delta("motivation", e.motivation_refund)


@_on(BonusRewards)
def _bonus(p: _Patch, e: BonusRewards) -> None:
    p.rewards(e.rewards)


@_on(ActivityCancelled)
def _cancelled(p: _Patch, e: ActivityCancelled) -> None:
    if e.result != "ok":
        return
    p.snap("busy", None)
    p.delta("motivation", e.motivation)
    p.delta("money", e.money)


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


@_on(SleepMenu)
def _sleep_menu(p: _Patch, e: SleepMenu) -> None:
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


@_on(RobberyFight)
def _robbery(p: _Patch, e: RobberyFight) -> None:
    _woke(p, e.rewards)


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


@_on(CardUsed)
def _card(p: _Patch, e: CardUsed) -> None:
    p.delta("money", e.money)
    p.delta("cards", -1)
    p.snap("card_ready_at", p.later(e.next_in_s))


@_on(GiftsScreen)
def _gifts(p: _Patch, e: GiftsScreen) -> None:
    p.snap("containers_small", e.containers_small)
    p.snap("containers_medium", e.containers_medium)
    if e.tangerines is not None:
        p.snap("tangerines", e.tangerines)


@_on(ContainerOpened)
def _container(p: _Patch, e: ContainerOpened) -> None:
    p.delta(f"containers_{e.size}", -1)


@_on(PrizeboxOpened)
def _prizebox(p: _Patch, e: PrizeboxOpened) -> None:
    p.snap("prizebox", False)
    p.snap("prizebox_ready_at", None)
    if e.money_after is not None:
        p.snap("money", e.money_after)


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


@_on(GorbushkaFight)
def _gorbushka_fight(p: _Patch, e: GorbushkaFight) -> None:
    p.rewards(e.rewards)
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


@_on(LevelUpStep)
def _levelup(p: _Patch, e: LevelUpStep) -> None:
    if e.step == "menu":
        p.snap("levelup_pending", True)
    elif e.step == "done":
        p.snap("levelup_pending", False)
        p.delta("money", e.money)
        p.delta("motivation", e.motivation)


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
        current = self._load(state)
        patch = _Patch(current, msg.date, msg.origin)
        applied = dict(current.applied)
        newest = max([*applied.values(), patch.origin])
        horizon = newest - OUTCOME_HORIZON
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
            return state
        dumped = dump_state(result)
        self._cache = (dumped, result)
        return dumped

    def metrics(self, old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, float]:
        out: dict[str, float] = {}
        for name in METRIC_FIELDS:
            before, after = old.get(name), new.get(name)
            if after is None or after.get("value") is None:
                continue
            if before is None or before.get("value") != after["value"]:
                out[name] = float(after["value"])
        return out
