import asyncio
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, not_, or_, select

from app.db.actions import UNRECONCILED_FREE
from app.db.base import Database
from app.db.models import (
    ActionRow,
    AuditRow,
    DecisionRow,
    LedgerRow,
    MessageRow,
    MetricRow,
    MetroRunRow,
    NotificationRow,
    ScenarioRunRow,
)
from app.engine.gametime import tasks_day
from app.engine.gateway.store import Obligation
from app.engine.gateway.types import ActionKind, ActionStatus
from app.engine.metro.store import METRO_HISTORY
from app.engine.server_settings import RetentionPolicy

BATCH = 5000


class DbRetention:
    """Удаление старых записей пачками: длинный DELETE держал бы блокировки против конвейера."""

    def __init__(self, db: Database, account_id: int, *, batch: int = BATCH) -> None:
        self._db = db
        self._account_id = account_id
        self._batch = batch

    async def purge(self, now: datetime, policy: RetentionPolicy) -> dict[str, int]:
        journal = now - timedelta(days=policy.messages_days)
        decisions = now - timedelta(days=policy.decisions_days)
        stats = now - timedelta(days=policy.metrics_days)
        ledger = tasks_day(now) - timedelta(days=policy.ledger_days - 1)
        # Несверенный неизвестный исход траты — обязательство сверки, оно живёт до сверки.
        open_obligation = and_(
            ActionRow.status == ActionStatus.OUTCOME_UNKNOWN.value,
            ActionRow.command_class.not_in(UNRECONCILED_FREE),
            ActionRow.reconciled_at.is_(None),
        )
        # Действие без трат (ход метро, /main) сверке не нужно и обязательством не считается.
        spend_free = await self._spend_free(open_obligation, ActionRow.created_at < journal)
        unfinished = (ActionStatus.INTENT.value, ActionStatus.SENT.value)
        # Бюджет метро — по p90 последних завершённых забегов, какими бы старыми они ни были.
        recent_metro = (
            select(MetroRunRow.id)
            .where(MetroRunRow.account_id == self._account_id, MetroRunRow.status == "done")
            .order_by(MetroRunRow.started_at.desc(), MetroRunRow.id.desc())
            .limit(METRO_HISTORY)
        )
        # Ручные действия и запуски с ключом — журнал ключей идемпотентности: удалив их, повтор
        # старого ключа исполнил бы команду снова. Их мало, они хранятся без срока.
        return {
            "messages": await self._purge(MessageRow, MessageRow.received_at < journal),
            "actions": await self._purge(
                ActionRow,
                ActionRow.created_at < journal,
                ActionRow.status.not_in(unfinished),
                or_(not_(open_obligation), ActionRow.id.in_(spend_free)),
                ActionRow.idempotency_key.is_(None),
            ),
            "scenario_runs": await self._purge(
                ScenarioRunRow,
                ScenarioRunRow.started_at < journal,
                ScenarioRunRow.status.not_in(("queued", "running")),
                ScenarioRunRow.idempotency_key.is_(None),
            ),
            "notifications": await self._purge(
                NotificationRow, NotificationRow.created_at < journal
            ),
            "decisions": await self._purge(DecisionRow, DecisionRow.at < decisions),
            "metrics": await self._purge(MetricRow, MetricRow.ts < stats),
            "metro_runs": await self._purge(
                MetroRunRow,
                MetroRunRow.started_at < stats,
                MetroRunRow.id.not_in(recent_metro.scalar_subquery()),
            ),
            "ledger": await self._purge(LedgerRow, LedgerRow.day < ledger),
        }

    async def purge_server(self, now: datetime, policy: RetentionPolicy) -> dict[str, int]:
        """Очистка уведомлений сервера и журнала аудита по политике сервера."""
        messages_cutoff = now - timedelta(days=policy.messages_days)
        audit_cutoff = now - timedelta(days=policy.audit_days)
        return {
            "notifications": await self._purge_table(
                NotificationRow,
                NotificationRow.account_id.is_(None),
                NotificationRow.created_at < messages_cutoff,
            ),
            "audit_log": await self._purge_table(
                AuditRow,
                AuditRow.at < audit_cutoff,
            ),
        }

    async def _spend_free(self, *conds: Any) -> list[int]:
        async with self._db.sessions() as session:
            rows = await session.execute(
                select(ActionRow.id, ActionRow.kind, ActionRow.payload).where(
                    ActionRow.account_id == self._account_id,
                    ActionRow.kind.in_((ActionKind.CLICK.value, ActionKind.SEND.value)),
                    *conds,
                )
            )
            return [
                row.id
                for row in rows
                if Obligation(
                    row.id, row.kind, row.payload.get("text"), row.payload.get("data")
                ).spends_nothing
            ]

    async def _purge(self, model: Any, *conds: Any) -> int:
        return await self._purge_table(model, model.account_id == self._account_id, *conds)

    async def _purge_table(self, model: Any, *conds: Any) -> int:
        total = 0
        while True:
            ids = select(model.id).where(*conds).limit(self._batch).scalar_subquery()
            async with self._db.sessions() as session, session.begin():
                result = await session.execute(
                    delete(model).where(model.id.in_(ids)).returning(model.id)
                )
                deleted = len(result.all())
            total += deleted
            if deleted < self._batch:
                return total
            await asyncio.sleep(0)
