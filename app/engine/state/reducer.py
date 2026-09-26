from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from app.engine.events import Event
from app.engine.parsing.activities import (
    ActivityCancelled,
    ActivityFinished,
    ActivityStarted,
    BonusRewards,
    MotivationFull,
)
from app.engine.parsing.battle import BattleTargetSet
from app.engine.parsing.common import Rewards
from app.engine.parsing.profile import ProfileCompact
from app.engine.parsing.refusals import Busy, Refused
from app.engine.state.model import (
    BusyState,
    CharacterState,
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

    def delta(self, name: str, diff: int) -> None:
        current: Obs[int] | None = self.get(name)
        if diff == 0 or current is None or current.at > self.at:
            return
        own = name in self.updates
        if own or current.at < self.origin:
            src: Src = "doubtful" if current.src == "doubtful" else "derived"
            self.updates[name] = Obs(value=current.value + diff, at=self.at, src=src)
            return
        # Снимок снят между созданием сообщения и его правкой (или в ту же секунду):
        # неизвестно, учёл ли он событие — значение не трогаем, но не доверяем ему.
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
        upgrades: Obs[Upgrades] | None = self.get("upgrades")
        if upgrades is not None and (r.upgrades_white or r.upgrades_blue or r.upgrades_red):
            self.snap(
                "upgrades",
                Upgrades(
                    white=upgrades.value.white + r.upgrades_white,
                    blue=upgrades.value.blue + r.upgrades_blue,
                    red=upgrades.value.red + r.upgrades_red,
                ),
                src="derived",
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
