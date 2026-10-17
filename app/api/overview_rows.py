"""Строка сводки по аккаунту: числа снимка, занятость бота и цель на битву."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from app.api.overview_battle import Source, account_battle_view, engine_kind
from app.api.routes_engine import IsoDatetime
from app.db.overview import AccountInput
from app.engine.battle_orders import BattleOrder
from app.engine.host.account import AccountRuntime
from app.engine.planner.loop import PlanView
from app.engine.planner.types import Act
from app.engine.state.model import CharacterState, Obs


class SkillsOut(BaseModel):
    practice: int
    theory: int
    cunning: int
    wisdom: int


class OverviewArtifactOut(BaseModel):
    artifact: str
    ends_at: IsoDatetime


class OverviewMetroOut(BaseModel):
    ready_at: IsoDatetime


class NextActionOut(BaseModel):
    kind: Literal["act", "wait"]
    # Сценарий действия; у ожидания его нет.
    scenario: str | None
    reason: str
    # Конец ожидания; null - до следующего события (и у действия).
    until: IsoDatetime | None
    # Код неготовности цикла (`paused`, `killed`, `manual_control`, `tg_offline`, …): пока он есть,
    # планировщик решений не исполняет.
    blocked_by: str | None


class OverviewBattleOut(BaseModel):
    battle_at: IsoDatetime
    desired: str
    source: Source
    # Цель, выставленная в игре на эту битву; null - неизвестно.
    in_game: str | None
    ready: bool | None
    # Приказ сейчас дойдёт до игры; иначе (или с оговоркой) причина в `note`: код готовности цикла,
    # `auto_off`, `scenario_running`, `engine_stopped`, `engine_elsewhere`, `dry_run`,
    # `feature_off`, `company_unknown`, `own_company`, `too_late`.
    applies: bool
    note: str | None


class OverviewAccountOut(BaseModel):
    id: int
    # Новейшее из наблюдений уровня и денег; null - снимка нет.
    seen_at: IsoDatetime | None
    # Снимок старше `engine.state_stale_after_min`: числа приглушаются.
    stale: bool
    exp: int | None
    exp_next: int | None
    money: int | None
    stamina: int | None
    motivation: int | None
    motivation_max: int | None
    knowledge: int | None
    details: int | None
    raw: int | None
    skills: SkillsOut | None
    # Идущий сбор артефакта; null - сбора нет.
    artifact: OverviewArtifactOut | None
    metro: OverviewMetroOut | None
    engine: Literal["local", "elsewhere", "stopped"]
    # Только у движка, запущенного здесь и ответившего вовремя.
    next_action: NextActionOut | None
    # null - ближайшая битва этому аккаунту неизвестна.
    battle: OverviewBattleOut | None


def _value[T](seen: Obs[T] | None) -> T | None:
    return seen.value if seen is not None else None


def _utc(moment: datetime | None) -> datetime | None:
    return moment.astimezone(UTC) if moment is not None else None


def _seen_at(state: CharacterState | None) -> datetime | None:
    if state is None:
        return None
    moments = [o.at for o in (state.level, state.money) if o is not None]
    return max(moments) if moments else None


def _skills(state: CharacterState | None) -> SkillsOut | None:
    seen = _value(state.skills) if state is not None else None
    return SkillsOut(**seen.model_dump()) if seen is not None else None


def skills_of(inp: AccountInput) -> SkillsOut | None:
    return _skills(inp.state)


def _next_action(plan: PlanView | None) -> NextActionOut | None:
    if plan is None:
        return None
    decision = plan.outlook.decision
    blocked = plan.loop.ready
    if isinstance(decision, Act):
        return NextActionOut(
            kind="act",
            scenario=decision.scenario,
            reason=decision.reason,
            until=None,
            blocked_by=blocked,
        )
    return NextActionOut(
        kind="wait",
        scenario=None,
        reason=decision.reason,
        until=_utc(decision.until),
        blocked_by=blocked,
    )


def _battle(
    inp: AccountInput,
    orders: Sequence[BattleOrder],
    battle: datetime | None,
    runtime: AccountRuntime | None,
    host_reason: str | None,
    plan: PlanView | None,
    now: datetime,
) -> OverviewBattleOut | None:
    if battle is None:
        return None
    view = account_battle_view(
        inp=inp,
        orders=orders,
        battle=battle,
        runtime=runtime,
        host_reason=host_reason,
        plan=plan,
        now=now,
        first=True,
    )
    return OverviewBattleOut(
        battle_at=battle.astimezone(UTC),
        desired=view.desired,
        source=view.source,
        in_game=view.in_game,
        ready=view.ready,
        applies=view.applies,
        note=view.note,
    )


def account_row(
    *,
    inp: AccountInput,
    orders: Sequence[BattleOrder],
    battle: datetime | None,
    runtime: AccountRuntime | None,
    host_reason: str | None,
    plan: PlanView | None,
    now: datetime,
) -> OverviewAccountOut:
    """`battle` - ближайшая битва, если этот аккаунт её наблюдал; иначе None."""
    state = inp.state
    seen_at = _seen_at(state)
    limit = timedelta(minutes=inp.settings.engine.state_stale_after_min)
    artifact = _value(state.artifact_collect) if state is not None else None
    ready_at = _value(state.metro_ready_at) if state is not None else None

    def number(name: str) -> int | None:
        return _value(getattr(state, name)) if state is not None else None

    return OverviewAccountOut(
        id=inp.info.id,
        seen_at=_utc(seen_at),
        stale=seen_at is not None and now - seen_at > limit,
        exp=number("exp"),
        exp_next=number("exp_next"),
        money=number("money"),
        stamina=number("stamina"),
        motivation=number("motivation"),
        motivation_max=number("motivation_max"),
        knowledge=number("knowledge"),
        details=number("details"),
        raw=number("raw"),
        skills=_skills(state),
        artifact=(
            OverviewArtifactOut(
                artifact=artifact.artifact, ends_at=artifact.ends_at.astimezone(UTC)
            )
            if artifact is not None
            else None
        ),
        metro=OverviewMetroOut(ready_at=ready_at.astimezone(UTC))
        if ready_at is not None
        else None,
        engine=engine_kind(runtime, host_reason),
        next_action=_next_action(plan) if runtime is not None else None,
        battle=_battle(inp, orders, battle, runtime, host_reason, plan, now),
    )
