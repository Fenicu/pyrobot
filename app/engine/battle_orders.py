from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, NamedTuple, Protocol

from app.engine.gametime import MSK, to_msk
from app.engine.parsing.battle import DEFENSE
from app.engine.parsing.common import COMPANIES
from app.engine.state.model import CharacterState

if TYPE_CHECKING:
    from app.engine.settings import BattleSection

# Часы битв по Москве, когда наблюдений нет (игра перешла на них 16.08.2026).
BATTLE_HOURS: tuple[int, ...] = (13, 22)


@dataclass(frozen=True, slots=True)
class BattleOrder:
    battle_at: datetime
    scope: str
    target: str


class TargetChoice(NamedTuple):
    target: str
    source: Literal["order_corp", "order_all", "override", "default"]


class BattleOrderSource(Protocol):
    """Откуда цикл планировщика берёт приказы штаба: приказы владельца на ближайшие битвы."""

    async def active(self, now: datetime) -> tuple[BattleOrder, ...]: ...


def targets_own_company(target: str, own: str | None) -> bool:
    return own is not None and COMPANIES.get(target) == own


def order_choice(
    orders: Sequence[BattleOrder], own: str | None, battle: datetime
) -> TargetChoice | None:
    """Цель по приказу штаба на битву `battle`; None - приказ аккаунту не касается."""
    if own is None:
        return None
    mine = [o for o in orders if o.battle_at == battle]
    for o in mine:
        if o.scope == own:
            return TargetChoice(_safe(o.target, own), "order_corp")
    for o in mine:
        if o.scope == "all":
            return TargetChoice(_safe(o.target, own), "order_all")
    return None


def choose_target(
    orders: Sequence[BattleOrder], own: str | None, battle: datetime, cfg: BattleSection
) -> TargetChoice:
    """Цель на битву: приказ штаба, затем своя на её час по Москве, затем общая."""
    ordered = order_choice(orders, own, battle)
    if ordered is not None:
        return ordered
    hour = to_msk(battle).hour
    if hour in cfg.overrides:
        return TargetChoice(cfg.overrides[hour], "override")
    return TargetChoice(cfg.target, "default")


def _safe(target: str, own: str) -> str:
    return DEFENSE if targets_own_company(target, own) else target


def target_is_set(state: CharacterState, battle: datetime, desired: str) -> bool | None:
    """Выставлена ли в игре цель `desired` на битву `battle`; None - неизвестно."""
    from app.engine.planner.base import battle_hour

    seen, known = state.battle_target, state.battle_at
    if (
        seen is not None
        and known is not None
        and seen.at >= known.at
        and seen.value is not None
        and battle_hour(known.value, known.at) == battle
    ):
        return bool(seen.value == desired)
    done = state.battle_target_set
    if done is None or battle_hour(done.value.battle_at, done.at) != battle:
        return None
    return bool(done.value.target == desired)


def next_battles(
    states: Iterable[CharacterState], now: datetime, hours: Sequence[int] = BATTLE_HOURS
) -> tuple[datetime, datetime | None]:
    """Ближайшая битва после `now` и следующая за ней (None - расписание сменилось)."""
    from app.engine.planner.base import battle_hour

    seen = [
        b
        for s in states
        if s.battle_at is not None and (b := battle_hour(s.battle_at.value, s.battle_at.at)) > now
    ]
    first = min(seen) if seen else _scheduled(now, hours)
    return first, _after(first, hours)


def _scheduled(now: datetime, hours: Sequence[int]) -> datetime:
    local = to_msk(now)
    days = (local.date() + timedelta(days=d) for d in (0, 1))
    return min(
        moment
        for day in days
        for h in hours
        if (moment := datetime(day.year, day.month, day.day, h, tzinfo=MSK).astimezone(UTC)) > now
    )


def _after(first: datetime, hours: Sequence[int]) -> datetime | None:
    local = to_msk(first)
    if local.hour not in hours or local.minute or local.second:
        return None
    ordered = sorted(hours)
    later = [h for h in ordered if h > local.hour]
    day = local.date() if later else local.date() + timedelta(days=1)
    hour = later[0] if later else ordered[0]
    return datetime(day.year, day.month, day.day, hour, tzinfo=MSK).astimezone(UTC)
