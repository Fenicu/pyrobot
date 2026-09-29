import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.db.base import Database
from app.db.models import ActionRow, NotificationRow
from app.engine.commands import CommandClass
from app.engine.gateway.store import (
    CANCELLED,
    LOST_FORWARD_CODE,
    Closed,
    DuplicateKey,
    Obligation,
    StoredAction,
    lost_forward_text,
)
from app.engine.gateway.types import ActionRequest, ActionStatus

log = logging.getLogger(__name__)

# Неизвестный исход навигации и пересылки состояние игры не меняет: их не сверяют.
UNRECONCILED_FREE = (CommandClass.NAV.value, CommandClass.FORWARD.value)
_FINAL = {
    ActionStatus.CONFIRMED,
    ActionStatus.REFUSED,
    ActionStatus.SUPPRESSED,
    ActionStatus.OUTCOME_UNKNOWN,
    ActionStatus.REJECTED,
}


def _stored(row: ActionRow) -> StoredAction:
    return StoredAction(row.id, ActionStatus(row.status), row.reason, row.answer, row.match_detail)


class DbActionStore:
    def __init__(self, db: Database, account_id: int) -> None:
        self._db = db
        self._account_id = account_id

    async def create(
        self, req: ActionRequest, cls: CommandClass, status: ActionStatus, reason: str = ""
    ) -> int:
        row = ActionRow(
            account_id=self._account_id,
            source=req.source.name.lower(),
            kind=req.kind.value,
            chat_id=req.chat_id,
            payload=req.payload(),
            command_class=cls.value,
            status=status.value,
            reason=reason[:200],
            idempotency_key=req.idempotency_key,
            scenario_run_id=req.scenario_run_id,
            finished_at=datetime.now(UTC) if status in _FINAL else None,
        )
        try:
            async with self._db.sessions() as session, session.begin():
                session.add(row)
                await session.flush()
                return int(row.id)
        except IntegrityError:
            existing = await self.get_by_key(req.idempotency_key or "")
            if existing is None:
                raise
            raise DuplicateKey(existing) from None

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
        values: dict[str, object] = {"status": status.value}
        if reason:
            values["reason"] = reason[:200]
        if attempts is not None:
            values["attempts"] = attempts
        if answer is not None:
            values["answer"] = answer
        if match_detail is not None:
            values["match_detail"] = match_detail
        now = datetime.now(UTC)
        if sent:
            values["sent_at"] = now
        if status in _FINAL:
            values["finished_at"] = now
        async with self._db.sessions() as session, session.begin():
            stmt = update(ActionRow).where(ActionRow.id == action_id).values(values)
            await session.execute(stmt)

    async def get_by_key(self, key: str) -> StoredAction | None:
        async with self._db.sessions() as session:
            row = await session.scalar(
                select(ActionRow).where(
                    ActionRow.account_id == self._account_id, ActionRow.idempotency_key == key
                )
            )
        return _stored(row) if row else None

    async def mark_unfinished_unknown(self) -> list[Closed]:
        async with self._db.sessions() as session, session.begin():
            rows = await session.execute(
                update(ActionRow)
                .where(
                    ActionRow.account_id == self._account_id,
                    or_(
                        ActionRow.status.in_([ActionStatus.INTENT.value, ActionStatus.SENT.value]),
                        and_(
                            ActionRow.status == ActionStatus.OUTCOME_UNKNOWN.value,
                            ActionRow.reason == CANCELLED,
                        ),
                    ),
                )
                .values(
                    status=ActionStatus.OUTCOME_UNKNOWN.value,
                    reason="restart",
                    finished_at=datetime.now(UTC),
                )
                .returning(ActionRow.id, ActionRow.command_class, ActionRow.payload)
            )
            closed = [
                Closed(int(i), CommandClass(cls), payload.get("message_id"))
                for i, cls, payload in rows
            ]
            lost = [c for c in closed if c.cls is CommandClass.FORWARD]
            session.add_all(
                NotificationRow(
                    account_id=self._account_id,
                    level="warn",
                    code=LOST_FORWARD_CODE,
                    text=lost_forward_text(c.message_id),
                )
                for c in lost
            )
        for c in lost:
            log.warning(lost_forward_text(c.message_id))
        return closed

    async def unreconciled(self) -> list[Obligation]:
        async with self._db.sessions() as session:
            rows = await session.scalars(
                select(ActionRow)
                .where(
                    ActionRow.account_id == self._account_id,
                    ActionRow.status == ActionStatus.OUTCOME_UNKNOWN.value,
                    ActionRow.command_class.not_in(UNRECONCILED_FREE),
                    ActionRow.reconciled_at.is_(None),
                )
                .order_by(ActionRow.id)
            )
            return [
                Obligation(row.id, row.kind, row.payload.get("text"), row.payload.get("data"))
                for row in rows
            ]

    async def mark_reconciled(self, action_ids: Sequence[int]) -> None:
        if not action_ids:
            return
        async with self._db.sessions() as session, session.begin():
            await session.execute(
                update(ActionRow)
                .where(ActionRow.account_id == self._account_id, ActionRow.id.in_(action_ids))
                .values(reconciled_at=datetime.now(UTC))
            )
