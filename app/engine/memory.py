from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.engine.commands import CommandClass
from app.engine.events import Event
from app.engine.gateway.store import DuplicateKey, StoredAction
from app.engine.gateway.types import ActionRequest, ActionStatus
from app.engine.types import IncomingMessage

_UNFINISHED = (ActionStatus.INTENT, ActionStatus.SENT)


class MemoryJournal:
    def __init__(self) -> None:
        self.rows: list[tuple[IncomingMessage, list[Event]]] = []
        self.snapshot: tuple[dict[str, Any], int] = ({}, 0)
        self._keys: set[tuple[int, int, int, str]] = set()

    async def load_state(self) -> tuple[dict[str, Any], int]:
        return self.snapshot

    async def append(
        self,
        msg: IncomingMessage,
        events: Sequence[Event],
        new_state: dict[str, Any] | None,
        new_version: int,
    ) -> int | None:
        key = (msg.chat_id, msg.msg_id, msg.revision, msg.content_hash())
        if key in self._keys:
            return None
        self._keys.add(key)
        self.rows.append((msg, list(events)))
        if new_state is not None:
            self.snapshot = (new_state, new_version)
        return len(self.rows)


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
        ids = [i for i, r in self.rows.items() if r.status in _UNFINISHED]
        for i in ids:
            await self.update(i, status=ActionStatus.OUTCOME_UNKNOWN, reason="restart")
        return ids
