import base64
import binascii
import json
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.container import Container
from app.api.deps import SessionContext, container, current_session
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


class ScenarioRunDetail(ScenarioRunOut):
    metro_run_id: int | None


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


def encode_cursor(key: FeedKey) -> str:
    raw = json.dumps([key.at.isoformat(), key.rank, key.id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> FeedKey:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        at, rank, ident = json.loads(raw)
        return FeedKey(datetime.fromisoformat(at), int(rank), int(ident))
    except (binascii.Error, ValueError, TypeError) as exc:
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


@router.get("/journal", response_model=JournalPage)
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
    after = decode_cursor(cursor) if cursor else None
    flt = FeedFilter(kinds, since, until, chat_id, status_, source)
    items = await c.reads.feed(flt, limit + 1, after)
    page = items[:limit]
    return JournalPage(
        items=[_item(i.row) for i in page],
        next_cursor=encode_cursor(page[-1].key) if len(items) > limit else None,
    )


@router.get("/decisions/{decision_id}", response_model=DecisionOut)
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


@router.get("/actions/{action_id}", response_model=ActionOut)
async def action(
    action_id: int,
    c: Annotated[Container, Depends(container)],
    _: Annotated[SessionContext, Depends(current_session)],
) -> ActionOut:
    row = await c.reads.action(action_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "action not found")
    return ActionOut.model_validate(row, from_attributes=True)


@router.get("/scenario-runs/{run_id}", response_model=ScenarioRunDetail)
async def scenario_run(
    run_id: int,
    c: Annotated[Container, Depends(container)],
    _: Annotated[SessionContext, Depends(current_session)],
) -> ScenarioRunDetail:
    found = await c.reads.scenario_run(run_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scenario run not found")
    row, metro_run_id = found
    return ScenarioRunDetail(**_run(row).model_dump(), metro_run_id=metro_run_id)
