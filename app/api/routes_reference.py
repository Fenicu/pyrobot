from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.cursor import decode_cursor, encode_cursor
from app.api.deps import SessionContext, require_csrf
from app.api.errors import AUTH, CSRF, not_found
from app.api.scope import AccountScope, account_router, account_scope
from app.db.models import MetroRunRow
from app.engine.state.reducer import METRIC_FIELDS

router = account_router("reference")
DEFAULT_WINDOW = timedelta(hours=24)
# Сценарии, после которых заметно меняются деньги, 🔋 и опыт: метки на графиках метрик.
EVENT_SCENARIOS = ("stocks_dump", "lottery_buy", "sleep", "gorbushka", "metro")
MAX_ACK = 500

Point = tuple[datetime, float]


class MetricEvent(BaseModel):
    at: datetime
    scenario: str


class MetricsOut(BaseModel):
    series: dict[str, list[Point]]
    # Последнее значение до начала окна (только на первой странице).
    initial: dict[str, Point]
    # Удачные запуски, которые двигают метрики, по концу в окне — метки на графиках (только на
    # первой странице): слив налички в акции, лотерея, сон, Горбушка, метро.
    events: list[MetricEvent]
    next_cursor: str | None


class MetroRunSummary(BaseModel):
    id: int
    scenario_run_id: int | None
    started_at: datetime
    finished_at: datetime | None
    status: str
    outcome: str
    steps: int
    duration_s: float
    step_s: float | None
    buffs: list[Any]
    result: dict[str, Any] | None
    summary: dict[str, Any]
    # Посещённых клеток (`grid.visited`): «шагов на клетку» в сводке списка.
    visited: int = 0


class MetroRunDetail(MetroRunSummary):
    grid: dict[str, Any]
    path: list[Any]
    events: list[Any]
    vitals: list[Any]


class MetroRunsPage(BaseModel):
    items: list[MetroRunSummary]
    next_before: int | None


class UnrecognizedOut(BaseModel):
    id: int
    message_id: int
    chat_id: int
    msg_id: int
    first_line: str
    text: str | None
    created_at: datetime
    acked: bool


class UnrecognizedPage(BaseModel):
    items: list[UnrecognizedOut]
    next_before: int | None


class AckIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=MAX_ACK)


class AckOut(BaseModel):
    acked: int


class NotificationOut(BaseModel):
    id: int
    created_at: datetime
    level: str
    code: str
    text: str
    read: bool


class NotificationsPage(BaseModel):
    items: list[NotificationOut]
    unread: int
    # Из непрочитанных — warn и error: их считает значок в меню, info (пауза, режим) — нет.
    unread_alerts: int
    next_before: int | None


class ReadIn(BaseModel):
    up_to_id: int = Field(ge=1)


class ReadOut(BaseModel):
    read: int


def _fields(raw: str | None) -> list[str]:
    if not raw:
        return list(METRIC_FIELDS)
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    unknown = [k for k in keys if k not in METRIC_FIELDS]
    if unknown or not keys:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown fields: {unknown}")
    return keys


@router.get("/metrics", response_model=MetricsOut, responses=AUTH)
async def metrics(
    scope: Annotated[AccountScope, Depends(account_scope)],
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
    fields: Annotated[str | None, Query(description="comma-separated metric keys")] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 2000,
) -> MetricsOut:
    keys = _fields(fields)
    end = until or datetime.now(UTC)
    start = since or end - DEFAULT_WINDOW
    after: tuple[datetime, int] | None = None
    if cursor:
        ts, ident = decode_cursor(cursor, 2)
        try:
            after = (datetime.fromisoformat(ts), int(ident))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid cursor") from exc
    points = await scope.reads.metrics(keys, start, end, limit + 1, after)
    page = points[:limit]
    series: dict[str, list[Point]] = {}
    for p in page:
        series.setdefault(p.key, []).append((p.ts, p.value))
    initial = {} if cursor else await scope.reads.metrics_before(keys, start)
    runs = [] if cursor else await scope.reads.runs_done(EVENT_SCENARIOS, start, end)
    last = page[-1] if page else None
    return MetricsOut(
        series=series,
        initial=initial,
        events=[MetricEvent(at=at, scenario=scenario) for at, scenario in runs],
        next_cursor=(
            encode_cursor([last.ts.isoformat(), last.id])
            if last is not None and len(points) > limit
            else None
        ),
    )


def _metro_run[T: MetroRunSummary](model: type[T], row: MetroRunRow) -> T:
    visited = row.grid.get("visited") if isinstance(row.grid, dict) else None
    count = len(visited) if isinstance(visited, list) else 0
    return model.model_validate(row, from_attributes=True).model_copy(update={"visited": count})


@router.get("/metro/runs", response_model=MetroRunsPage, responses=AUTH)
async def metro_runs(
    scope: Annotated[AccountScope, Depends(account_scope)],
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MetroRunsPage:
    rows = await scope.reads.metro_runs(limit + 1, before)
    page = rows[:limit]
    return MetroRunsPage(
        items=[_metro_run(MetroRunSummary, r) for r in page],
        next_before=page[-1].id if len(rows) > limit else None,
    )


@router.get(
    "/metro/runs/{run_id}",
    response_model=MetroRunDetail,
    responses={**AUTH, **not_found("metro run")},
)
async def metro_run(
    run_id: int,
    scope: Annotated[AccountScope, Depends(account_scope)],
) -> MetroRunDetail:
    row = await scope.reads.metro_run(run_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "metro run not found")
    return _metro_run(MetroRunDetail, row)


@router.get("/unrecognized", response_model=UnrecognizedPage, responses=AUTH)
async def unrecognized(
    scope: Annotated[AccountScope, Depends(account_scope)],
    acked: Literal["false", "true", "all"] = "false",
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> UnrecognizedPage:
    flag = None if acked == "all" else acked == "true"
    items = await scope.reads.unrecognized(flag, limit + 1, before)
    page = items[:limit]
    return UnrecognizedPage(
        items=[
            UnrecognizedOut(
                id=i.row.id,
                message_id=i.row.message_id,
                chat_id=i.row.chat_id,
                msg_id=i.row.msg_id,
                first_line=i.row.first_line,
                text=i.text,
                created_at=i.row.created_at,
                acked=i.row.acked,
            )
            for i in page
        ],
        next_before=page[-1].row.id if len(items) > limit else None,
    )


@router.post("/unrecognized/ack", response_model=AckOut, responses=CSRF)
async def ack_unrecognized(
    body: AckIn,
    _: Annotated[SessionContext, Depends(require_csrf)],
    scope: Annotated[AccountScope, Depends(account_scope)],
) -> AckOut:
    return AckOut(acked=await scope.reads.ack_unrecognized(body.ids))


@router.get("/notifications", response_model=NotificationsPage, responses=AUTH)
async def notifications(
    scope: Annotated[AccountScope, Depends(account_scope)],
    unread: bool = False,
    level: Literal["info", "warn", "error"] | None = None,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> NotificationsPage:
    rows, total, alerts = await scope.reads.notifications(
        unread=unread, level=level, limit=limit + 1, before=before
    )
    page = rows[:limit]
    return NotificationsPage(
        items=[NotificationOut.model_validate(r, from_attributes=True) for r in page],
        unread=total,
        unread_alerts=alerts,
        next_before=page[-1].id if len(rows) > limit else None,
    )


@router.post("/notifications/read", response_model=ReadOut, responses=CSRF)
async def read_notifications(
    body: ReadIn,
    _: Annotated[SessionContext, Depends(require_csrf)],
    scope: Annotated[AccountScope, Depends(account_scope)],
) -> ReadOut:
    return ReadOut(read=await scope.reads.read_notifications(body.up_to_id))
