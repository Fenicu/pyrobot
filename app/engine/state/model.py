from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger(__name__)
Src = Literal["screen", "derived", "doubtful"]
SCHEMA_VERSION = 1


class Obs[T](BaseModel):
    model_config = ConfigDict(frozen=True)
    value: T
    at: datetime
    src: Src = "screen"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Skills(_Frozen):
    practice: int
    theory: int
    cunning: int
    wisdom: int


class BusyState(_Frozen):
    activity: str
    until: datetime


class PriceState(_Frozen):
    motivation: int = 0
    money: int = 0
    minutes: int = 0
    details: int = 0
    white: int = 0
    blue: int = 0
    knowledge: int = 0


class FoodStockState(_Frozen):
    count: int
    low: int
    high: int


class Upgrades(_Frozen):
    white: int
    blue: int
    red: int


class GorbushkaState(_Frozen):
    state: str
    won: int | None = None
    total: int | None = None
    ticket_until: datetime | None = None
    next_fight_at: datetime | None = None
    comeback_at: datetime | None = None
    fight_cost: int | None = None


class RefusalState(_Frozen):
    reason: str
    need: int | None = None


class TeamTask(_Frozen):
    current: int
    goal: int
    resource: str


class ActivityStat(_Frozen):
    """Скользящее среднее наград одного дела (без его цены)."""

    count: int = 0
    exp: float = 0
    money: float = 0
    knowledge: float = 0
    details: float = 0
    raw: float = 0


# Стартовые значения — средние по корпусу (mechanics_core §2.4, живой замер переработки).
DEED_PRIORS = {
    "harvest": ActivityStat(exp=203),
    "job": ActivityStat(exp=117, money=25.7, details=2.3, raw=0.8),
    "learn": ActivityStat(exp=235, knowledge=11),
    "dconv": ActivityStat(exp=253, raw=5),
}
DEFAULT_PRICES = {
    "harvest": PriceState(motivation=1, money=30, minutes=5),
    "job": PriceState(motivation=1, minutes=2),
    "learn": PriceState(motivation=2, minutes=4),
    "dconv": PriceState(motivation=1, money=5, details=10, minutes=6),
    "eat": PriceState(money=5, minutes=5),
    "walk": PriceState(motivation=1, minutes=5),
    "confa": PriceState(motivation=3, money=7, minutes=10),
    "rob": PriceState(motivation=1, minutes=8),
}


class CharacterState(_Frozen):
    schema_version: int = SCHEMA_VERSION
    level: Obs[int] | None = None
    exp: Obs[int] | None = None
    exp_next: Obs[int] | None = None
    money: Obs[int] | None = None
    stamina: Obs[int] | None = None
    knowledge: Obs[int] | None = None
    raw: Obs[int] | None = None
    details: Obs[int] | None = None
    motivation: Obs[int] | None = None
    motivation_max: Obs[int] | None = None
    motivation_next_at: Obs[datetime | None] | None = None
    bag: Obs[int] | None = None
    bag_cap: Obs[int] | None = None
    tangerines: Obs[int] | None = None
    skills: Obs[Skills] | None = None
    battle_at: Obs[datetime] | None = None
    battle_target: Obs[str | None] | None = None
    busy: Obs[BusyState | None] | None = None
    sleep_deadline: Obs[datetime | None] | None = None
    woke_at: Obs[datetime] | None = None
    sleep_allowed_at: Obs[datetime | None] | None = None
    levelup_pending: Obs[bool] | None = None
    prices: dict[str, Obs[PriceState]] = {}
    food_stock: Obs[dict[str, FoodStockState]] | None = None
    fastfood_ready_at: Obs[datetime] | None = None
    books: Obs[int] | None = None
    book_ready_at: Obs[datetime] | None = None
    cards: Obs[int] | None = None
    card_ready_at: Obs[datetime] | None = None
    prizebox: Obs[bool] | None = None
    prizebox_ready_at: Obs[datetime | None] | None = None
    containers_small: Obs[int] | None = None
    containers_medium: Obs[int] | None = None
    upgrades: Obs[Upgrades] | None = None
    gorbushka: Obs[GorbushkaState] | None = None
    last_refusal: Obs[RefusalState] | None = None
    team_task: Obs[TeamTask] | None = None
    activity_stats: dict[str, ActivityStat] = {}
    # Ключи «чат:сообщение:вид» применённых итогов → время создания сообщения:
    # правка итога не начисляет повторно (горизонт хранения — в редьюсере).
    applied: dict[str, datetime] = {}


def load_state(data: dict[str, Any]) -> CharacterState:
    if not data:
        return CharacterState()
    if data.get("schema_version") != SCHEMA_VERSION:
        log.warning("state snapshot schema %r ignored", data.get("schema_version"))
        return CharacterState()
    try:
        return CharacterState.model_validate(data)
    except ValidationError:
        log.exception("state snapshot invalid, starting empty")
        return CharacterState()


def dump_state(state: CharacterState) -> dict[str, Any]:
    return state.model_dump(mode="json")


def is_fresh(obs: Obs[Any] | None, now: datetime, max_age: timedelta) -> bool:
    return obs is not None and obs.src != "doubtful" and now - obs.at <= max_age


VOLATILE = frozenset(
    {"money", "stamina", "motivation", "busy", "exp", "knowledge", "raw", "details"}
)
TIMERS = frozenset(
    {
        "motivation_next_at",
        "battle_at",
        "battle_target",
        "sleep_deadline",
        "woke_at",
        "sleep_allowed_at",
        "levelup_pending",
        "fastfood_ready_at",
        "book_ready_at",
        "card_ready_at",
        "prizebox_ready_at",
        "last_refusal",
    }
)
SLOW_MAX_AGE = timedelta(hours=6)
PRICE_MAX_AGE = timedelta(days=7)


def stale_fields(state: CharacterState, now: datetime, volatile_max_age: timedelta) -> list[str]:
    names: list[str] = []
    for name in CharacterState.model_fields:
        obs = getattr(state, name)
        if not isinstance(obs, Obs):
            continue
        if name in TIMERS:
            if obs.src == "doubtful":
                names.append(name)
            continue
        max_age = volatile_max_age if name in VOLATILE else SLOW_MAX_AGE
        if not is_fresh(obs, now, max_age):
            names.append(name)
    names.extend(
        f"prices.{key}"
        for key, obs in state.prices.items()
        if not is_fresh(obs, now, PRICE_MAX_AGE)
    )
    return names
