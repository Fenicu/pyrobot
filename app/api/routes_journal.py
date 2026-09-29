from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.container import Container
from app.api.cursor import decode_cursor, encode_cursor
from app.api.deps import SessionContext, container, current_session
from app.api.errors import AUTH, not_found
from app.db.models import ActionRow, DecisionRow, MessageRow, ScenarioRunRow
from app.db.reads import FeedFilter, FeedKey, feed_types

router = APIRouter(prefix="/api/v1", tags=["journal"])


class MessageItem(BaseModel):
    type: Literal["message"] = "message"
    id: int
    at: datetime
    chat_id: int
    msg_id: int
    revision: int
    kind: str
    date: datetime
    outgoing: bool
    recovered: bool
    text: str | None
    markup: dict[str, Any] | None
    events: list[Any]


class ActionItem(BaseModel):
    type: Literal["action"] = "action"
    id: int
    at: datetime
    source: str
    kind: str
    chat_id: int
    command_class: str
    status: str
    reason: str
    text: str | None
    data: str | None
    # Сообщение действия (клик — по чему, пересылка — что) и название чата пересылки.
    message_id: int | None = None
    chat_title: str | None = None
    finished_at: datetime | None


class DecisionItem(BaseModel):
    type: Literal["decision"] = "decision"
    id: int
    at: datetime
    kind: str
    scenario: str | None
    reason: str
    until: datetime | None


JournalItem = Annotated[MessageItem | ActionItem | DecisionItem, Field(discriminator="type")]


class JournalPage(BaseModel):
    items: list[JournalItem]
    next_cursor: str | None


class ScenarioRunOut(BaseModel):
    id: int
    decision_id: int | None
    scenario: str
    params: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None
    status: str
    reason: str
    requested_by: str | None


class ScenarioRunsPage(BaseModel):
    items: list[ScenarioRunOut]
    next_before: int | None


class DecisionOut(BaseModel):
    id: int
    at: datetime
    kind: str
    scenario: str | None
    params: dict[str, Any]
    reason: str
    until: datetime | None
    candidates: list[Any]
    runs: list[ScenarioRunOut]


class ActionOut(BaseModel):
    id: int
    created_at: datetime
    source: str
    kind: str
    chat_id: int
    payload: dict[str, Any]
    command_class: str
    status: str
    reason: str
    attempts: int
    answer: str | None
    match_detail: str | None
    sent_at: datetime | None
    finished_at: datetime | None
    reconciled_at: datetime | None
    # Ключ идемпотентности (`manual:<ключ>` у ручных команд) и запуск, шагом которого было
    # действие.
    idempotency_key: str | None
    scenario_run_id: int | None


class ScenarioRunDetail(ScenarioRunOut):
    metro_run_id: int | None
    # Действия шагов запуска в порядке создания.
    actions: list[ActionOut]


def _feed_cursor(key: FeedKey) -> str:
    return encode_cursor([key.at.isoformat(), key.rank, key.id])


def _feed_key(cursor: str) -> FeedKey:
    at, rank, ident = decode_cursor(cursor, 3)
    try:
        return FeedKey(datetime.fromisoformat(at), int(rank), int(ident))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid cursor") from exc


def _item(row: Any) -> MessageItem | ActionItem | DecisionItem:
    if isinstance(row, MessageRow):
        return MessageItem(
            id=row.id,
            at=row.received_at,
            chat_id=row.chat_id,
            msg_id=row.msg_id,
            revision=row.revision,
            kind=row.kind,
            date=row.date,
            outgoing=row.outgoing,
            recovered=row.recovered,
            text=row.text,
            markup=row.markup,
            events=row.events,
        )
    if isinstance(row, ActionRow):
        return ActionItem(
            id=row.id,
            at=row.created_at,
            source=row.source,
            kind=row.kind,
            chat_id=row.chat_id,
            command_class=row.command_class,
            status=row.status,
            reason=row.reason,
            text=row.payload.get("text"),
            data=row.payload.get("data"),
            message_id=row.payload.get("message_id"),
            chat_title=row.payload.get("chat_title"),
            finished_at=row.finished_at,
        )
    assert isinstance(row, DecisionRow)
    return DecisionItem(
        id=row.id,
        at=row.at,
        kind=row.kind,
        scenario=row.scenario,
        reason=row.reason,
        until=row.until,
    )


def _run(row: ScenarioRunRow) -> ScenarioRunOut:
    return ScenarioRunOut.model_validate(row, from_attributes=True)


@router.get("/journal", response_model=JournalPage, responses=AUTH)
async def journal(
    c: Annotated[Container, Depends(container)],
    _: Annotated[SessionContext, Depends(current_session)],
    types: Annotated[str | None, Query(description="message,action,decision")] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    chat_id: int | None = None,
    status_: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    source: Annotated[str | None, Query(max_length=16)] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JournalPage:
    try:
        kinds = feed_types(types)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    after = _feed_key(cursor) if cursor else None
    flt = FeedFilter(kinds, since, until, chat_id, status_, source)
    items = await c.reads.feed(flt, limit + 1, after)
    page = items[:limit]
    return JournalPage(
        items=[_item(i.row) for i in page],
        next_cursor=_feed_cursor(page[-1].key) if len(items) > limit else None,
    )


@router.get(
    "/decisions/{decision_id}",
    response_model=DecisionOut,
    responses={**AUTH, **not_found("decision")},
)
async def decision(
    decision_id: int,
    c: Annotated[Container, Depends(container)],
    _: Annotated[SessionContext, Depends(current_session)],
) -> DecisionOut:
    found = await c.reads.decision(decision_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "decision not found")
    row, runs = found
    return DecisionOut(
        id=row.id,
        at=row.at,
        kind=row.kind,
        scenario=row.scenario,
        params=row.params,
        reason=row.reason,
        until=row.until,
        candidates=row.candidates,
        runs=[_run(r) for r in runs],
    )


@router.get(
    "/actions/{action_id}", response_model=ActionOut, responses={**AUTH, **not_found("action")}
)
async def action(
    action_id: int,
    c: Annotated[Container, Depends(container)],
    _: Annotated[SessionContext, Depends(current_session)],
) -> ActionOut:
    row = await c.reads.action(action_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "action not found")
    return ActionOut.model_validate(row, from_attributes=True)


@router.get("/scenario-runs", response_model=ScenarioRunsPage, responses=AUTH)
async def scenario_runs(
    c: Annotated[Container, Depends(container)],
    _: Annotated[SessionContext, Depends(current_session)],
    manual: bool | None = None,
    scenario: Annotated[str | None, Query(max_length=32)] = None,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ScenarioRunsPage:
    rows = await c.reads.scenario_runs(
        manual=manual, scenario=scenario, limit=limit + 1, before=before
    )
    page = rows[:limit]
    return ScenarioRunsPage(
        items=[_run(r) for r in page],
        next_before=page[-1].id if len(rows) > limit else None,
    )


@router.get(
    "/scenario-runs/{run_id}",
    response_model=ScenarioRunDetail,
    responses={**AUTH, **not_found("scenario run")},
)
async def scenario_run(
    run_id: int,
    c: Annotated[Container, Depends(container)],
    _: Annotated[SessionContext, Depends(current_session)],
) -> ScenarioRunDetail:
    found = await c.reads.scenario_run(run_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scenario run not found")
    row, metro_run_id = found
    actions = await c.reads.run_actions(run_id)
    return ScenarioRunDetail(
        **_run(row).model_dump(),
        metro_run_id=metro_run_id,
        actions=[ActionOut.model_validate(a, from_attributes=True) for a in actions],
    )
