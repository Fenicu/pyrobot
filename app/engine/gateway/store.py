from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.engine.commands import CommandClass
from app.engine.gateway.types import ActionRequest, ActionResult, ActionStatus, Match, Verdict


@dataclass(frozen=True, slots=True)
class StoredAction:
    id: int
    status: ActionStatus
    reason: str
    answer: str | None = None
    match_detail: str | None = None

    def to_result(self) -> ActionResult:
        match = None
        if self.match_detail is not None and self.status in (
            ActionStatus.CONFIRMED,
            ActionStatus.REFUSED,
        ):
            match = Match(Verdict(self.status.value), self.match_detail)
        return ActionResult(self.status, self.id, self.reason, match, self.answer)


@dataclass(frozen=True, slots=True)
class Obligation:
    """Действие с неизвестным исходом, по которому ещё не сверено состояние."""

    action_id: int | None
    kind: str
    text: str | None = None
    data: str | None = None


@dataclass(frozen=True, slots=True)
class Closed:
    """Действие прошлого процесса, закрытое при старте как outcome_unknown: класс и сообщение
    (`payload.message_id`: клик — по чему, пересылка — что)."""

    action_id: int
    cls: CommandClass
    message_id: int | None = None


# Причина outcome_unknown для действия, прерванного остановкой шлюза посреди отправки;
# такие строки при следующем старте сверяются так же, как незавершённые.
CANCELLED = "cancelled"


class DuplicateKey(Exception):
    def __init__(self, existing: StoredAction) -> None:
        super().__init__(f"idempotency key already used by action {existing.id}")
        self.existing = existing


class ActionStore(Protocol):
    async def create(
        self, req: ActionRequest, cls: CommandClass, status: ActionStatus, reason: str = ""
    ) -> int: ...

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
    ) -> None: ...

    async def get_by_key(self, key: str) -> StoredAction | None: ...

    async def mark_unfinished_unknown(self) -> list[Closed]: ...

    async def unreconciled(self) -> list[Obligation]: ...

    async def mark_reconciled(self, action_ids: Sequence[int]) -> None: ...
