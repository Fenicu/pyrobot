"""Обзор аккаунтов учётки: штаб битвы. Всё в границах аккаунтов `Account.owner_id`
пользователя сессии; чужой и несуществующий аккаунт здесь неразличимы - их просто нет."""

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, get_args

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.container import Container
from app.api.deps import SessionContext, container, current_session, require_csrf
from app.api.errors import AUTH, CSRF, ValidationErrorOut, error
from app.api.overview_battle import (
    UNMANAGED,
    BattleView,
    Source,
    account_battle_view,
    engine_kind,
    fetch_plans,
    knows_battle,
)
from app.api.overview_rows import OverviewAccountOut, SkillsOut, account_row, skills_of
from app.api.routes_engine import IsoDatetime
from app.db.accounts import AccountInfo
from app.db.overview import AccountInput
from app.engine.battle_orders import BattleOrder, next_battles, targets_own_company
from app.engine.gametime import to_msk
from app.engine.parsing.common import COMPANIES
from app.engine.planner.obligations import TARGET_LAST_CALL
from app.engine.settings import Target

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])

# Приказ живёт до своей битвы; с запасом на разбор уже начавшейся.
ORDERS_LOOKBACK = timedelta(hours=2)
_TARGETS: tuple[str, ...] = get_args(Target)
_COMPANY_NAMES = {code: name for name, code in COMPANIES.items()}
BATTLE_TOO_CLOSE = "battle_too_close"
BATTLE_NOT_OFFERED = "battle_not_offered"


class HqTargetOut(BaseModel):
    name: str
    # Код компании в командах игры; у защиты его нет.
    code: str | None


class HqCompanyOut(BaseModel):
    code: str
    name: str
    # Включённых аккаунтов учётки с этой компанией.
    accounts: int


class SkippedOut(BaseModel):
    account_id: int
    # `disabled`, `error` или `blocked`: в штаб такой аккаунт не входит.
    reason: str


class BattleOrdersOut(BaseModel):
    all: str | None
    corporations: dict[str, str]


class BattleAccountOut(BaseModel):
    id: int
    name: str
    company: str | None
    level: int | None
    skills: SkillsOut | None
    # Цель по приоритету: приказ корпорации, приказ «всем», цель на час битвы, общая.
    desired: str
    source: Source
    # Цель, выставленная в игре на эту битву; null - неизвестно (у следующей битвы всегда).
    in_game: str | None
    ready: bool | None
    # Цель сейчас дойдёт до игры; иначе (или с оговоркой) причина в `note`: код готовности цикла
    # (`paused`, `killed`, `manual_control`, `tg_offline`, …), `auto_off`, `scenario_running`,
    # `engine_stopped`, `engine_elsewhere`, `dry_run`, `feature_off`, `company_unknown`,
    # `own_company`, `too_late`.
    applies: bool
    note: str | None
    engine: Literal["local", "elsewhere", "stopped"]


class BattleSummaryOut(BaseModel):
    # null - строка «цель не управляется ботом»: выключена механика битвы или нет движка здесь.
    target: str | None
    count: int
    level_sum: int
    level_avg: float | None
    # Аккаунтов без известного уровня: они в счёте, но не в суммах.
    unknown: int
    skills: SkillsOut
    account_ids: list[int]
    # Сколько из них уже выставили цель в игре.
    ready: int


class BattleOut(BaseModel):
    battle_at: IsoDatetime
    hour_msk: int
    in_s: int
    # За `TARGET_LAST_CALL` до начала приказ уже не применится.
    editable: bool
    note: Literal["too_late"] | None
    orders: BattleOrdersOut
    accounts: list[BattleAccountOut]
    summary: list[BattleSummaryOut]


class BattleHqOut(BaseModel):
    now: IsoDatetime
    # observed - ближайшая битва по наблюдениям аккаунтов, schedule - по часам 13:00 и 22:00 МСК.
    source: Literal["observed", "schedule"]
    targets: list[HqTargetOut]
    companies: list[HqCompanyOut]
    # Ближайшая и следующая; следующей нет, если расписание игры сменилось.
    battles: list[BattleOut]
    skipped: list[SkippedOut]


class BattleOrdersIn(BaseModel):
    all: str | None = None
    corporations: dict[str, str] = Field(default_factory=dict)


class NotWokenOut(BaseModel):
    account_id: int
    # `locked_elsewhere` - движок на другом хосте, `stopped` - не запущен.
    reason: str


class BattleOrdersSavedOut(BattleOut):
    # Движки этого процесса, которым передано уведомление: доставка, а не применение.
    woken: list[int]
    not_woken: list[NotWokenOut]


def _states(inputs: Sequence[AccountInput]) -> list[Any]:
    return [i.state for i in inputs if i.state is not None]


def _skipped_reason(info: AccountInfo) -> str | None:
    if info.blocked:
        return "blocked"
    return None if info.status == "enabled" else info.status


def _summary(rows: list[BattleAccountOut]) -> list[BattleSummaryOut]:
    managed: dict[str | None, list[BattleAccountOut]] = {}
    for t in _TARGETS:
        group = [r for r in rows if r.note not in UNMANAGED and r.desired == t]
        if group:
            managed[t] = group
    unmanaged = [r for r in rows if r.note in UNMANAGED]
    if unmanaged:
        managed[None] = unmanaged
    return [_summary_row(t, group) for t, group in managed.items()]


def _summary_row(target: str | None, group: list[BattleAccountOut]) -> BattleSummaryOut:
    levels = [r.level for r in group if r.level is not None]
    skills = [r.skills for r in group if r.skills is not None]
    return BattleSummaryOut(
        target=target,
        count=len(group),
        level_sum=sum(levels),
        level_avg=round(sum(levels) / len(levels), 1) if levels else None,
        unknown=len(group) - len(levels),
        skills=SkillsOut(
            practice=sum(s.practice for s in skills),
            theory=sum(s.theory for s in skills),
            cunning=sum(s.cunning for s in skills),
            wisdom=sum(s.wisdom for s in skills),
        ),
        account_ids=[r.id for r in group],
        ready=sum(1 for r in group if r.ready is True),
    )


def _orders_out(orders: Sequence[BattleOrder], battle: datetime) -> BattleOrdersOut:
    mine = [o for o in orders if o.battle_at == battle]
    return BattleOrdersOut(
        all=next((o.target for o in mine if o.scope == "all"), None),
        corporations={o.scope: o.target for o in mine if o.scope != "all"},
    )


def _battle_out(
    *,
    battle: datetime,
    first: bool,
    now: datetime,
    shown: Sequence[AccountInput],
    orders: Sequence[BattleOrder],
    c: Container,
    plans: dict[int, Any],
) -> BattleOut:
    rows: list[BattleAccountOut] = []
    for inp in shown:
        account_id = inp.info.id
        runtime = c.engines.get(account_id)
        reason = c.engines.host_reason(account_id)
        view: BattleView = account_battle_view(
            inp=inp,
            orders=orders,
            battle=battle,
            runtime=runtime,
            host_reason=reason,
            plan=plans.get(account_id),
            now=now,
            first=first,
        )
        state = inp.state
        level = state.level if state is not None else None
        company = state.company if state is not None else None
        rows.append(
            BattleAccountOut(
                id=account_id,
                name=inp.info.name,
                company=company.value if company is not None else None,
                level=level.value if level is not None else None,
                skills=skills_of(inp),
                desired=view.desired,
                source=view.source,
                in_game=view.in_game,
                ready=view.ready,
                applies=view.applies,
                note=view.note,
                engine=engine_kind(runtime, reason),
            )
        )
    left = battle - now
    too_late = left <= TARGET_LAST_CALL
    return BattleOut(
        battle_at=battle.astimezone(UTC),
        hour_msk=to_msk(battle).hour,
        in_s=max(0, int(left.total_seconds())),
        editable=not too_late,
        note="too_late" if too_late else None,
        orders=_orders_out(orders, battle),
        accounts=rows,
        summary=_summary(rows),
    )


def _observed(inputs: Sequence[AccountInput], now: datetime) -> bool:
    return any(knows_battle(i.state, now) for i in inputs)


async def _hq(
    c: Container, owner_id: int, now: datetime, inputs: Sequence[AccountInput]
) -> BattleHqOut:
    first, second = next_battles(_states(inputs), now)
    orders = await c.battle_orders.active(owner_id, now - ORDERS_LOOKBACK)
    shown = [i for i in inputs if _skipped_reason(i.info) is None]
    skipped = [
        SkippedOut(account_id=i.info.id, reason=reason)
        for i in inputs
        if (reason := _skipped_reason(i.info)) is not None
    ]
    plans = await fetch_plans({i.info.id: c.engines.get(i.info.id) for i in shown})
    battles = [
        _battle_out(
            battle=battle, first=index == 0, now=now, shown=shown, orders=orders, c=c, plans=plans
        )
        for index, battle in enumerate(b for b in (first, second) if b is not None)
    ]
    counts = Counter(
        company.value
        for i in shown
        if i.state is not None and (company := i.state.company) is not None
        if company.value is not None
    )
    return BattleHqOut(
        now=now,
        source="observed" if _observed(inputs, now) else "schedule",
        targets=[HqTargetOut(name=t, code=COMPANIES.get(t)) for t in _TARGETS],
        companies=[
            HqCompanyOut(code=code, name=_COMPANY_NAMES[code], accounts=counts[code])
            for code in COMPANIES.values()
            if counts[code]
        ],
        battles=battles,
        skipped=skipped,
    )


@router.get("/battle", response_model=BattleHqOut, responses=AUTH)
async def battle_hq(
    ctx: Annotated[SessionContext, Depends(current_session)],
    c: Annotated[Container, Depends(container)],
) -> BattleHqOut:
    """Штаб битвы: ближайшая и следующая битва, приказы на них и цель каждого аккаунта учётки."""
    inputs = await c.overview.account_inputs(ctx.user_id)
    return await _hq(c, ctx.user_id, c.clock.now(), inputs)


def _parse_battle(raw: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _issue(loc: list[str], kind: str, msg: str) -> dict[str, Any]:
    return {"loc": ["body", *loc], "msg": msg, "type": kind}


def _order_issues(body: BattleOrdersIn) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if body.all is not None and body.all not in _TARGETS:
        issues.append(_issue(["all"], "unknown_target", "unknown target"))
    codes = set(COMPANIES.values())
    for code, target in body.corporations.items():
        loc = ["corporations", code]
        if code not in codes:
            issues.append(_issue(loc, "unknown_company", "unknown company"))
        if target not in _TARGETS:
            issues.append(_issue(loc, "unknown_target", "unknown target"))
        elif targets_own_company(target, code):
            issues.append(_issue(loc, "target_is_own_company", "corporation cannot attack itself"))
    return issues


@router.put(
    "/battle/{battle_at}/orders",
    response_model=BattleOrdersSavedOut,
    responses={
        **CSRF,
        409: error(BATTLE_TOO_CLOSE),
        422: {"model": ValidationErrorOut, "description": BATTLE_NOT_OFFERED},
    },
)
async def put_battle_orders(
    battle_at: str,
    body: BattleOrdersIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> BattleOrdersSavedOut:
    """Заменить приказы на битву целиком (пустой набор - отмена). Последняя запись побеждает.
    Движки будятся после записи; `woken` - доставка уведомления, не применение."""
    now = c.clock.now()
    inputs = await c.overview.account_inputs(ctx.user_id)
    battle = _parse_battle(battle_at)
    if battle is None or battle not in {b for b in next_battles(_states(inputs), now) if b}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, BATTLE_NOT_OFFERED)
    if battle - now <= TARGET_LAST_CALL:
        raise HTTPException(status.HTTP_409_CONFLICT, BATTLE_TOO_CLOSE)
    if issues := _order_issues(body):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, issues)
    stored = {"all": body.all} if body.all is not None else {}
    await c.battle_orders.replace(
        ctx.user_id, battle.astimezone(UTC), stored | body.corporations, ctx.login
    )
    owned = [a.id for a in await c.accounts.owned(ctx.user_id)]
    woken = c.engines.orders_changed(owned)
    hq = await _hq(c, ctx.user_id, now, inputs)
    [saved] = [b for b in hq.battles if b.battle_at == battle]
    listed = {a.id for a in saved.accounts}
    return BattleOrdersSavedOut(
        **dict(saved),
        woken=woken,
        not_woken=[
            NotWokenOut(account_id=i, reason=c.engines.host_reason(i) or "stopped")
            for i in owned
            if i in listed and i not in woken
        ],
    )


@router.get("/accounts", response_model=list[OverviewAccountOut], responses=AUTH)
async def overview_accounts(
    ctx: Annotated[SessionContext, Depends(current_session)],
    c: Annotated[Container, Depends(container)],
) -> list[OverviewAccountOut]:
    """Сводка по аккаунтам учётки: числа снимка, действие бота и цель на ближайшую битву.
    Базовые поля аккаунта (статус, пауза, непрочитанные) - в `GET /accounts`."""
    now = c.clock.now()
    inputs = await c.overview.account_inputs(ctx.user_id)
    first, _ = next_battles(_states(inputs), now)
    orders = await c.battle_orders.active(ctx.user_id, now - ORDERS_LOOKBACK)
    runtimes = {i.info.id: c.engines.get(i.info.id) for i in inputs}
    plans = await fetch_plans(runtimes)
    return [
        account_row(
            inp=i,
            orders=orders,
            battle=first if knows_battle(i.state, now) else None,
            runtime=runtimes[i.info.id],
            host_reason=c.engines.host_reason(i.info.id),
            plan=plans[i.info.id],
            now=now,
        )
        for i in inputs
    ]
