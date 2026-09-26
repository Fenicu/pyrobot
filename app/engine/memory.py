from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.engine.commands import CommandClass
from app.engine.events import Event, Unrecognized
from app.engine.gateway.store import CANCELLED, DuplicateKey, Obligation, StoredAction
from app.engine.gateway.types import ActionRequest, ActionStatus
from app.engine.types import IncomingMessage

_UNFINISHED = (ActionStatus.INTENT, ActionStatus.SENT)


class MemoryJournal:
    def __init__(self) -> None:
        self.rows: list[tuple[IncomingMessage, list[Event]]] = []
        self.snapshot: tuple[dict[str, Any], int] = ({}, 0)
        self.metrics: list[tuple[datetime, str, float]] = []
        self.unrecognized: list[tuple[int, str]] = []
        self._keys: set[tuple[int, int, int, str]] = set()

    async def load_state(self) -> tuple[dict[str, Any], int]:
        return self.snapshot

    async def revisions(self, chat_id: int, msg_id: int) -> list[IncomingMessage]:
        return [m for m, _ in self.rows if (m.chat_id, m.msg_id) == (chat_id, msg_id)]

    async def append(
        self,
        msg: IncomingMessage,
        events: Sequence[Event],
        new_state: dict[str, Any] | None,
        new_version: int,
        metrics: Mapping[str, float] | None = None,
    ) -> int | None:
        key = (msg.chat_id, msg.msg_id, msg.revision, msg.content_hash())
        if key in self._keys:
            return None
        self._keys.add(key)
        self.rows.append((msg, list(events)))
        journal_id = len(self.rows)
        if new_state is not None:
            self.snapshot = (new_state, new_version)
        self.metrics.extend((msg.date, k, v) for k, v in (metrics or {}).items())
        self.unrecognized.extend(
            (journal_id, e.first_line) for e in events if isinstance(e, Unrecognized)
        )
        return journal_id


@dataclass
class MemoryActionRow:
    req: ActionRequest
    cls: CommandClass
    status: ActionStatus
    reason: str = ""
    attempts: int = 0
    answer: str | None = None
    match_detail: str | None = None
    sent: bool = False
    reconciled: bool = False
    history: list[ActionStatus] = field(default_factory=list)


class MemoryActionStore:
    def __init__(self) -> None:
        self.rows: dict[int, MemoryActionRow] = {}
        self._keys: dict[str, int] = {}

    def _stored(self, action_id: int) -> StoredAction:
        row = self.rows[action_id]
        return StoredAction(action_id, row.status, row.reason, row.answer, row.match_detail)

    async def create(
        self, req: ActionRequest, cls: CommandClass, status: ActionStatus, reason: str = ""
    ) -> int:
        if req.idempotency_key and req.idempotency_key in self._keys:
            raise DuplicateKey(self._stored(self._keys[req.idempotency_key]))
        action_id = len(self.rows) + 1
        self.rows[action_id] = MemoryActionRow(req, cls, status, reason, history=[status])
        if req.idempotency_key:
            self._keys[req.idempotency_key] = action_id
        return action_id

    async def update(
        self,
        action_id: int,
        *,
        status: ActionStatus,
        reason: str = "",
        attempts: int | None = None,
        answer: str | None = None,
        match_detail: str | None = None,
        sent: bool = False,
    ) -> None:
        row = self.rows[action_id]
        row.status = status
        row.reason = reason or row.reason
        row.history.append(status)
        if attempts is not None:
            row.attempts = attempts
        if answer is not None:
            row.answer = answer
        if match_detail is not None:
            row.match_detail = match_detail
        row.sent = row.sent or sent

    async def get_by_key(self, key: str) -> StoredAction | None:
        action_id = self._keys.get(key)
        return self._stored(action_id) if action_id is not None else None

    async def mark_unfinished_unknown(self) -> list[int]:
        ids = [
            i
            for i, r in self.rows.items()
            if r.status in _UNFINISHED
            or (r.status is ActionStatus.OUTCOME_UNKNOWN and r.reason == CANCELLED)
        ]
        for i in ids:
            await self.update(i, status=ActionStatus.OUTCOME_UNKNOWN, reason="restart")
        return ids

    async def unreconciled(self) -> list[Obligation]:
        return [
            Obligation(i, r.req.kind.value, r.req.text, r.req.data)
            for i, r in self.rows.items()
            if r.status is ActionStatus.OUTCOME_UNKNOWN
            and r.cls is not CommandClass.NAV
            and not r.reconciled
        ]

    async def mark_reconciled(self, action_ids: Sequence[int]) -> None:
        for action_id in action_ids:
            if action_id in self.rows:
                self.rows[action_id].reconciled = True
