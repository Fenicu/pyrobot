"""Цель на битву по аккаунту: общая для штаба и сводки обзора."""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.db.overview import AccountInput
from app.engine.battle_orders import (
    BattleOrder,
    TargetChoice,
    choose_target,
    target_is_set,
)
from app.engine.facade import PlannerUnavailable
from app.engine.host.account import AccountRuntime
from app.engine.planner.base import battle_hour
from app.engine.planner.loop import PlanView
from app.engine.planner.obligations import TARGET_LAST_CALL
from app.engine.state.model import CharacterState

log = logging.getLogger(__name__)

# Сколько обзор ждёт «План бота» одного движка: медленный не задерживает остальных.
OUTLOOK_TIMEOUT_S = 2.0
Source = Literal["order_corp", "order_all", "override", "default"]
# Цель этих аккаунтов бот не ставит вообще: в сводку по целям они не входят.
UNMANAGED = frozenset({"feature_off", "engine_stopped", "engine_elsewhere"})


@dataclass(frozen=True, slots=True)
class BattleView:
    """`ready` - цель выставлена в игре (None - неизвестно); `applies` - приказ сейчас дойдёт
    до игры, иначе (или с оговоркой) `note`."""

    desired: str
    source: Source
    in_game: str | None
    ready: bool | None
    applies: bool
    note: str | None


def engine_kind(
    runtime: AccountRuntime | None, host_reason: str | None
) -> Literal["local", "elsewhere", "stopped"]:
    if runtime is not None:
        return "local"
    return "elsewhere" if host_reason == "locked_elsewhere" else "stopped"


def knows_battle(state: CharacterState | None, now: datetime) -> bool:
    """Аккаунт наблюдал предстоящую битву."""
    seen = state.battle_at if state is not None else None
    return seen is not None and battle_hour(seen.value, seen.at) > now


async def fetch_plans(
    runtimes: Mapping[int, AccountRuntime | None],
) -> dict[int, PlanView | None]:
    """«План бота» запущенных здесь движков, параллельно; недоступный и упавший - None."""

    async def one(runtime: AccountRuntime | None) -> PlanView | None:
        facade = runtime.facade if runtime is not None else None
        if facade is None:
            return None
        try:
            return await asyncio.wait_for(facade.outlook(), OUTLOOK_TIMEOUT_S)
        except (PlannerUnavailable, TimeoutError):
            return None
        except Exception:
            log.warning("outlook failed", exc_info=True)
            return None

    ids = list(runtimes)
    views = await asyncio.gather(*(one(runtimes[i]) for i in ids))
    return dict(zip(ids, views, strict=True))


def account_battle_view(
    *,
    inp: AccountInput,
    orders: Sequence[BattleOrder],
    battle: datetime,
    runtime: AccountRuntime | None,
    host_reason: str | None,
    plan: PlanView | None,
    now: datetime,
    first: bool,
) -> BattleView:
    state = inp.state
    own = _company(state)
    choice = choose_target(orders, own, battle, inp.settings.battle)
    in_game: str | None = None
    ready: bool | None = None
    if first and state is not None:
        in_game = _in_game(state, battle)
        ready = target_is_set(state, battle, choice.target)
    applies, note = _applicability(
        inp, orders, battle, runtime, host_reason, plan, now, choice, own
    )
    return BattleView(choice.target, choice.source, in_game, ready, applies, note)


def _company(state: CharacterState | None) -> str | None:
    seen = state.company if state is not None else None
    return seen.value if seen is not None else None


def _in_game(state: CharacterState, battle: datetime) -> str | None:
    """Цель, выставленная на битву `battle`; наблюдения другой битвы - None."""
    seen, known = state.battle_target, state.battle_at
    if (
        seen is not None
        and known is not None
        and seen.at >= known.at
        and seen.value is not None
        and battle_hour(known.value, known.at) == battle
    ):
        return seen.value
    done = state.battle_target_set
    if done is not None and battle_hour(done.value.battle_at, done.at) == battle:
        return done.value.target
    return None


def _applicability(
    inp: AccountInput,
    orders: Sequence[BattleOrder],
    battle: datetime,
    runtime: AccountRuntime | None,
    host_reason: str | None,
    plan: PlanView | None,
    now: datetime,
    choice: TargetChoice,
    own: str | None,
) -> tuple[bool, str | None]:
    settings = inp.settings
    if runtime is None:
        return False, "engine_elsewhere" if host_reason == "locked_elsewhere" else "engine_stopped"
    if not settings.features.battle:
        return False, "feature_off"
    if settings.engine.mode == "dry_run":
        return False, "dry_run"
    if battle - now <= TARGET_LAST_CALL:
        return False, "too_late"
    loop = plan.loop if plan is not None else None
    blocked = loop.ready if loop is not None else _blocked_by_settings(inp)
    if blocked is not None:
        return False, blocked
    if loop is not None and not loop.auto:
        return False, "auto_off"
    if own is None and any(o.battle_at == battle for o in orders):
        return False, "company_unknown"
    if loop is not None and loop.current is not None:
        return True, "scenario_running"
    if choice.source in ("order_corp", "order_all") and _forced_defense(
        orders, battle, choice, own
    ):
        return True, "own_company"
    return True, None


def _blocked_by_settings(inp: AccountInput) -> str | None:
    """Без «Плана бота» причину неготовности видно только по настройкам."""
    engine = inp.settings.engine
    if engine.killed:
        return "killed"
    return "paused" if engine.paused else None


def _forced_defense(
    orders: Sequence[BattleOrder], battle: datetime, choice: TargetChoice, own: str | None
) -> bool:
    """Приказ целил в свою компанию аккаунта, и тот ушёл в защиту."""
    scope = own if choice.source == "order_corp" else "all"
    raw = next((o.target for o in orders if o.battle_at == battle and o.scope == scope), None)
    return raw is not None and raw != choice.target
