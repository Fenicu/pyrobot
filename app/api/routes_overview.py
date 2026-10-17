"""Обзор аккаунтов учётки: штаб битвы. Всё в границах аккаунтов `Account.owner_id`
пользователя сессии; чужой и несуществующий аккаунт здесь неразличимы - их просто нет."""

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal, cast, get_args

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AfterValidator, BaseModel, Field

from app.api.container import Container
from app.api.cursor import decode_cursor, encode_cursor
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
from app.api.routes_daily import KindOut
from app.api.routes_engine import IsoDatetime
from app.db.accounts import AccountInfo
from app.db.overview import FEED_TYPES, AccountInput, FeedCursor, FeedEvent
from app.engine.battle_orders import (
    ORDERS_LOOKBACK,
    BattleOrder,
    next_battles,
    targets_own_company,
)
from app.engine.daily import (
    AccountTotals,
    KindSum,
    Totals,
    account_totals,
    merge_totals,
    summarize,
)
from app.engine.gametime import day_start, tasks_day, to_msk
from app.engine.parsing.common import COMPANIES
from app.engine.planner.obligations import TARGET_LAST_CALL
from app.engine.settings import Target

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])

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


PERIODS = (1, 7, 30)


def _period(days: int) -> int:
    if days not in PERIODS:
        raise ValueError("days must be 1, 7 or 30")
    return days


# Числовой Literal в query не принимает строку "7", поэтому проверка - после разбора числа.
Period = Annotated[int, AfterValidator(_period)]


class TotalDeltaOut(BaseModel):
    # Сумма изменений по аккаунтам, у которых оно известно; null - не известно ни у одного.
    delta: int | None
    # Известно у всех аккаунтов (`accounts` из них вошли в сумму; клиент пишет «по 5 из 7»).
    covered: bool
    accounts: int


class TotalsBlockOut(BaseModel):
    # Изменение за период по money, exp, knowledge, details, raw, glory.
    balance: dict[str, TotalDeltaOut]
    # Повышения уровня; null - окно длиннее хранения метрик.
    levels: int | None
    # 🏆 за задания (вид `task` журнала), а не личная слава `glory`.
    trophies: int
    items: dict[str, int]
    income: list[KindOut]
    losses: list[KindOut]


class TotalsDayOut(BaseModel):
    day: date
    # Сегодня или день до запуска журнала прихода: разовое неизвестно, столбец штрихуется.
    partial: bool
    # Столбцы совпадают с «Итогами дня» аккаунта; null - нет данных.
    balance: dict[str, int | None]


class TotalsAccountOut(BaseModel):
    id: int
    ledger_since: date | None
    totals: TotalsBlockOut
    # Сегодня первым.
    days: list[TotalsDayOut]


class TotalsOut(BaseModel):
    now: IsoDatetime
    days: int
    first: date
    today: date
    # Метрики раньше этого момента не хранятся: окно, начавшееся раньше, не покрыто.
    metrics_floor: IsoDatetime
    total: TotalsBlockOut
    accounts: list[TotalsAccountOut]


def _kinds(sums: tuple[KindSum, ...]) -> list[KindOut]:
    return [KindOut(kind=s.kind, count=s.count, amounts=s.amounts) for s in sums]


def _block(t: Totals) -> TotalsBlockOut:
    return TotalsBlockOut(
        balance={
            k: TotalDeltaOut(delta=d.delta, covered=d.covered, accounts=d.accounts)
            for k, d in t.balance.items()
        },
        levels=t.levels,
        trophies=t.trophies,
        items=t.items,
        income=_kinds(t.income),
        losses=_kinds(t.losses),
    )


@router.get("/totals", response_model=TotalsOut, responses=AUTH)
async def overview_totals(
    ctx: Annotated[SessionContext, Depends(current_session)],
    c: Annotated[Container, Depends(container)],
    days: Annotated[Period, Query(json_schema_extra={"enum": list(PERIODS)})],
) -> TotalsOut:
    """Итоги периода по всем аккаунтам учётки: целые сутки МСК, сегодня на текущий момент.
    Баланс - последнее значение метрики в окне минус последнее до окна; суммы журнала прихода и
    столбцы суток считаются как в «Итогах дня»."""
    now = c.clock.now()
    today = tasks_day(now)
    first = today - timedelta(days=days - 1)
    floor = now - timedelta(days=c.server_settings.current.retention.metrics_days)
    ids = [a.id for a in await c.accounts.owned(ctx.user_id)]
    sources = await c.overview.totals(ctx.user_id, ids, first, now, floor)
    covered = day_start(first) >= floor
    rows: list[tuple[TotalsAccountOut, AccountTotals]] = []
    for src in sources:
        summary = summarize(
            today=today,
            days=days,
            last=src.last,
            level_before=None,
            ledger=src.ledger,
            ledger_since=src.ledger_since,
        )
        part = account_totals(
            summary, last=src.last, first=first, before=src.before, window_covered=covered
        )
        rows.append(
            (
                TotalsAccountOut(
                    id=src.account_id,
                    ledger_since=src.ledger_since,
                    totals=_block(merge_totals([part])),
                    days=[
                        TotalsDayOut(
                            day=d.day,
                            partial=d.partial,
                            balance={k: b.delta for k, b in d.balance.items()},
                        )
                        for d in summary
                    ],
                ),
                part,
            )
        )
    return TotalsOut(
        now=now,
        days=days,
        first=first,
        today=today,
        metrics_floor=floor,
        total=_block(merge_totals([part for _, part in rows])),
        accounts=[out for out, _ in rows],
    )


FeedType = Literal["levelup", "battle", "factory", "metro", "lottery", "alert", "manual"]
MAX_FEED_ACCOUNT_IDS = 200
# Границы int4 (accounts.id) и int8 (id журнала и уведомлений): большее не пройдёт в БД.
_INT4_MAX = 2**31 - 1
_INT8_MAX = 2**63 - 1


class FeedItemOut(BaseModel):
    # `l:<id>` - запись журнала прихода, `n:<id>` - уведомление: по ключу клиент убирает повторы.
    key: str
    # Время события: у журнала - исхода (отчёт о битве датирован битвой), у уведомления - создания.
    at: IsoDatetime
    account_id: int
    type: FeedType
    # Вид журнала прихода или код уведомления.
    kind: str
    # Только у уведомления.
    level: str | None
    text: str | None
    # Только у записи журнала; текст собирает клиент из вида и сумм.
    amounts: dict[str, int] | None
    items: dict[str, int] | None


class FeedWatermarkOut(BaseModel):
    ledger: int
    notifications: int


class FeedOut(BaseModel):
    items: list[FeedItemOut]
    # Курсор следующей (более старой) страницы; null - дальше ничего нет и при `after`.
    next_cursor: str | None
    # Знак приёма: у первой страницы и у ответа на `after`; у страницы по курсору null (клиент
    # держит знак первой страницы).
    watermark: FeedWatermarkOut | None
    # Есть ещё записи: старее страницы (по курсору) или новее знака (при `after`).
    has_more: bool


def _feed_item(e: FeedEvent) -> FeedItemOut:
    return FeedItemOut(
        key=e.key,
        at=e.at,
        account_id=e.account_id,
        type=cast(FeedType, e.type),
        kind=e.kind,
        level=e.level,
        text=e.text,
        amounts=e.amounts,
        items=e.items,
    )


def _feed_types(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return list(FEED_TYPES)
    wanted = [t.strip() for t in raw.split(",") if t.strip()]
    unknown = [t for t in wanted if t not in FEED_TYPES]
    if unknown or not wanted:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown types: {unknown}")
    return [t for t in FEED_TYPES if t in wanted]


def _feed_accounts(raw: str | None) -> list[int] | None:
    if raw is None or not raw.strip():
        return None
    try:
        ids = list(dict.fromkeys(int(p) for p in raw.split(",") if p.strip()))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid account_ids") from exc
    if any(i > _INT4_MAX for i in ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid account_ids")
    if len(ids) > MAX_FEED_ACCOUNT_IDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "too many account_ids")
    return ids


def _feed_cursor(raw: str) -> FeedCursor:
    at, rank, ident = decode_cursor(raw, 3)
    try:
        cursor = FeedCursor(datetime.fromisoformat(at), int(rank), int(ident))
        # Год 1 с положительным поясом не переводится в UTC: OverflowError, а не 500 в драйвере БД
        cursor.at.astimezone(UTC)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid cursor") from exc
    if not 0 <= cursor.id <= _INT8_MAX:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid cursor")
    return cursor


def _feed_after(raw: str) -> tuple[int, int]:
    ledger, _, notes = raw.partition(":")
    if not (ledger.isascii() and ledger.isdigit() and notes.isascii() and notes.isdigit()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid after")
    marks = int(ledger), int(notes)
    if max(marks) > _INT8_MAX:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid after")
    return marks


@router.get(
    "/feed",
    response_model=FeedOut,
    responses={**AUTH, 422: {"model": ValidationErrorOut, "description": "invalid parameters"}},
)
async def overview_feed(
    ctx: Annotated[SessionContext, Depends(current_session)],
    c: Annotated[Container, Depends(container)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(description="курсор следующей страницы")] = None,
    account_ids: Annotated[str | None, Query(description="id аккаунтов через запятую")] = None,
    types: Annotated[str | None, Query(description=",".join(FEED_TYPES))] = None,
    after: Annotated[
        str | None, Query(description="знак приёма <id журнала>:<id уведомлений>")
    ] = None,
) -> FeedOut:
    """Единая лента событий аккаунтов учётки: записи журнала прихода (уровень, битва, фабрика,
    метро, лотерея) и уведомления (предупреждения и ошибки, вмешательства оператора).

    Страница - от новых к старым по времени события, дальше по `next_cursor`. Свежее первой
    страницы - по знаку приёма `watermark`: `after=<знак>` отдаёт записи, пришедшие позже (по id,
    а не по времени события), порциями до `limit`, пока `has_more`. Только читает: `read`
    уведомлений не меняется."""
    if cursor is not None and after is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "cursor and after are exclusive"
        )
    kinds = _feed_types(types)
    ids = _feed_accounts(account_ids)
    now = c.clock.now()
    floor = tasks_day(now) - timedelta(days=c.server_settings.current.retention.ledger_days - 1)
    if after is not None:
        got = await c.overview.feed_after(
            ctx.user_id, ids, kinds, after=_feed_after(after), limit=limit, ledger_floor=floor
        )
        next_cursor = None
    else:
        got = await c.overview.feed_page(
            ctx.user_id,
            ids,
            kinds,
            limit=limit,
            cursor=_feed_cursor(cursor) if cursor else None,
            ledger_floor=floor,
        )
        last = got.events[-1] if got.has_more and got.events else None
        next_cursor = encode_cursor([last.at.isoformat(), last.rank, last.id]) if last else None
    return FeedOut(
        items=[_feed_item(e) for e in got.events],
        next_cursor=next_cursor,
        watermark=FeedWatermarkOut(ledger=got.watermark[0], notifications=got.watermark[1])
        if got.watermark is not None
        else None,
        has_more=got.has_more,
    )
