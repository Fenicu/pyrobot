from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on

from app.db.base import Database
from app.db.models import (
    ActionRow,
    DecisionRow,
    MessageRow,
    MetricRow,
    MetroRunRow,
    NotificationRow,
    ScenarioRunRow,
    SettingsHistory,
    UnrecognizedRow,
)
from app.engine.settings import Settings, settings_diff

# Порядок типов записей на одном моменте: сообщение, потом действие, потом решение.
FEED_TYPES = ("message", "action", "decision")
FeedRow = MessageRow | ActionRow | DecisionRow
_FEED_MODELS: dict[str, Any] = {
    "message": (MessageRow, MessageRow.received_at),
    "action": (ActionRow, ActionRow.created_at),
    "decision": (DecisionRow, DecisionRow.at),
}


@dataclass(frozen=True, slots=True)
class FeedKey:
    """Позиция записи в ленте: момент, ранг типа, id — лента идёт по убыванию ключа."""

    at: datetime
    rank: int
    id: int


@dataclass(frozen=True, slots=True)
class FeedFilter:
    types: tuple[str, ...] = FEED_TYPES
    since: datetime | None = None
    until: datetime | None = None
    chat_id: int | None = None
    status: str | None = None
    source: str | None = None

    def narrowed(self) -> tuple[str, ...]:
        types = self.types
        # Статус и источник есть только у действий, чат — у сообщений и действий.
        if self.status is not None or self.source is not None:
            types = tuple(t for t in types if t == "action")
        if self.chat_id is not None:
            types = tuple(t for t in types if t != "decision")
        return types


@dataclass(frozen=True, slots=True)
class FeedItem:
    type: str
    key: FeedKey
    row: FeedRow


@dataclass(frozen=True, slots=True)
class MetricPoint:
    id: int
    ts: datetime
    key: str
    value: float


@dataclass(frozen=True, slots=True)
class UnrecognizedItem:
    row: UnrecognizedRow
    text: str | None


@dataclass(frozen=True, slots=True)
class SettingsVersion:
    version: int
    changed_by: str
    changed_at: datetime
    changes: dict[str, list[Any]]


class DbReads:
    """Выборки для админки: журнал, справочное, история настроек (только чтение и ack)."""

    def __init__(self, db: Database, account_id: int) -> None:
        self._db = db
        self._account_id = account_id

    async def feed(self, flt: FeedFilter, limit: int, after: FeedKey | None) -> list[FeedItem]:
        """До `limit` записей ленты старше `after`, от новых к старым (слияние трёх таблиц)."""
        items: list[FeedItem] = []
        async with self._db.sessions() as session:
            for kind in flt.narrowed():
                rank = FEED_TYPES.index(kind)
                model, at = _FEED_MODELS[kind]
                query: Select[Any] = (
                    select(model)
                    .where(model.account_id == self._account_id)
                    .order_by(at.desc(), model.id.desc())
                    .limit(limit)
                )
                query = _feed_where(query, model, at, rank, flt, after)
                for row in await session.scalars(query):
                    moment = getattr(row, at.key)
                    items.append(FeedItem(kind, FeedKey(moment, rank, row.id), row))
        items.sort(key=lambda i: (i.key.at, i.key.rank, i.key.id), reverse=True)
        return items[:limit]

    async def decision(self, decision_id: int) -> tuple[DecisionRow, list[ScenarioRunRow]] | None:
        async with self._db.sessions() as session:
            row = await session.get(DecisionRow, decision_id)
            if row is None or row.account_id != self._account_id:
                return None
            runs = await session.scalars(
                select(ScenarioRunRow)
                .where(ScenarioRunRow.decision_id == decision_id)
                .order_by(ScenarioRunRow.id)
            )
            return row, list(runs)

    async def action(self, action_id: int) -> ActionRow | None:
        async with self._db.sessions() as session:
            row = await session.get(ActionRow, action_id)
        return row if row is not None and row.account_id == self._account_id else None

    async def action_by_key(self, key: str) -> ActionRow | None:
        async with self._db.sessions() as session:
            return await session.scalar(
                select(ActionRow).where(
                    ActionRow.account_id == self._account_id, ActionRow.idempotency_key == key
                )
            )

    async def scenario_run(self, run_id: int) -> tuple[ScenarioRunRow, int | None] | None:
        async with self._db.sessions() as session:
            row = await session.get(ScenarioRunRow, run_id)
            if row is None or row.account_id != self._account_id:
                return None
            metro = await session.scalar(
                select(MetroRunRow.id)
                .where(MetroRunRow.scenario_run_id == run_id)
                .order_by(MetroRunRow.id.desc())
                .limit(1)
            )
            return row, metro

    async def metrics(
        self,
        keys: Sequence[str],
        since: datetime,
        until: datetime,
        limit: int,
        after: tuple[datetime, int] | None,
    ) -> list[MetricPoint]:
        """Точки окна `[since, until)` по возрастанию `(ts, id)`, строго после `after`."""
        query = (
            select(MetricRow)
            .where(
                MetricRow.account_id == self._account_id,
                MetricRow.key.in_(keys),
                MetricRow.ts >= since,
                MetricRow.ts < until,
            )
            .order_by(MetricRow.ts, MetricRow.id)
            .limit(limit)
        )
        if after is not None:
            ts, ident = after
            query = query.where(
                or_(MetricRow.ts > ts, and_(MetricRow.ts == ts, MetricRow.id > ident))
            )
        async with self._db.sessions() as session:
            rows = await session.scalars(query)
            return [MetricPoint(r.id, r.ts, r.key, r.value) for r in rows]

    async def metrics_before(
        self, keys: Sequence[str], moment: datetime
    ) -> dict[str, tuple[datetime, float]]:
        """Последнее значение каждого ключа до `moment` — значение на начало окна."""
        query = (
            select(MetricRow.key, MetricRow.ts, MetricRow.value)
            .where(
                MetricRow.account_id == self._account_id,
                MetricRow.key.in_(keys),
                MetricRow.ts < moment,
            )
            .order_by(MetricRow.key, MetricRow.ts.desc(), MetricRow.id.desc())
            .ext(distinct_on(MetricRow.key))
        )
        async with self._db.sessions() as session:
            rows = await session.execute(query)
            return {key: (ts, value) for key, ts, value in rows.all()}

    async def metro_runs(self, limit: int, before: int | None) -> list[MetroRunRow]:
        query = (
            select(MetroRunRow)
            .where(MetroRunRow.account_id == self._account_id)
            .order_by(MetroRunRow.id.desc())
            .limit(limit)
        )
        if before is not None:
            query = query.where(MetroRunRow.id < before)
        async with self._db.sessions() as session:
            return list(await session.scalars(query))

    async def metro_run(self, run_id: int) -> MetroRunRow | None:
        async with self._db.sessions() as session:
            row = await session.get(MetroRunRow, run_id)
        return row if row is not None and row.account_id == self._account_id else None

    async def unrecognized(
        self, acked: bool | None, limit: int, before: int | None
    ) -> list[UnrecognizedItem]:
        query = (
            select(UnrecognizedRow, MessageRow.text)
            .join(MessageRow, MessageRow.id == UnrecognizedRow.message_id)
            .where(UnrecognizedRow.account_id == self._account_id)
            .order_by(UnrecognizedRow.id.desc())
            .limit(limit)
        )
        if acked is not None:
            query = query.where(UnrecognizedRow.acked.is_(acked))
        if before is not None:
            query = query.where(UnrecognizedRow.id < before)
        async with self._db.sessions() as session:
            rows = await session.execute(query)
            return [UnrecognizedItem(row, text) for row, text in rows.all()]

    async def ack_unrecognized(self, ids: Sequence[int]) -> int:
        async with self._db.sessions() as session, session.begin():
            done = await session.scalars(
                update(UnrecognizedRow)
                .where(
                    UnrecognizedRow.account_id == self._account_id,
                    UnrecognizedRow.id.in_(ids),
                    UnrecognizedRow.acked.is_(False),
                )
                .values(acked=True)
                .returning(UnrecognizedRow.id)
            )
            return len(done.all())

    async def notifications(
        self, *, unread: bool, level: str | None, limit: int, before: int | None
    ) -> tuple[list[NotificationRow], int]:
        """Страница уведомлений от новых к старым и общее число непрочитанных."""
        own = NotificationRow.account_id == self._account_id
        query = select(NotificationRow).where(own).order_by(NotificationRow.id.desc()).limit(limit)
        if unread:
            query = query.where(NotificationRow.read.is_(False))
        if level is not None:
            query = query.where(NotificationRow.level == level)
        if before is not None:
            query = query.where(NotificationRow.id < before)
        count = (
            select(func.count())
            .select_from(NotificationRow)
            .where(own, NotificationRow.read.is_(False))
        )
        async with self._db.sessions() as session:
            rows = list(await session.scalars(query))
            total = await session.scalar(count)
        return rows, int(total or 0)

    async def read_notifications(self, up_to_id: int) -> int:
        async with self._db.sessions() as session, session.begin():
            done = await session.scalars(
                update(NotificationRow)
                .where(
                    NotificationRow.account_id == self._account_id,
                    NotificationRow.id <= up_to_id,
                    NotificationRow.read.is_(False),
                )
                .values(read=True)
                .returning(NotificationRow.id)
            )
            return len(done.all())

    async def settings_history(self, limit: int, before: int | None) -> list[SettingsVersion]:
        query = (
            select(SettingsHistory)
            .where(SettingsHistory.account_id == self._account_id)
            .order_by(SettingsHistory.version.desc())
            .limit(limit + 1)
        )
        if before is not None:
            query = query.where(SettingsHistory.version < before)
        async with self._db.sessions() as session:
            rows = list(await session.scalars(query))
        defaults = Settings().model_dump(mode="json")
        out = []
        # Лишняя строка — предыдущая версия последней на странице; у самой первой — дефолты.
        for row, prev in zip(rows[:limit], [*rows[1:], None], strict=False):
            base = prev.data if prev is not None else defaults
            out.append(
                SettingsVersion(
                    row.version, row.changed_by, row.changed_at, settings_diff(base, row.data)
                )
            )
        return out


def _feed_where(
    query: Select[Any],
    model: Any,
    at: Any,
    rank: int,
    flt: FeedFilter,
    after: FeedKey | None,
) -> Select[Any]:
    conds: list[Any] = []
    if flt.since is not None:
        conds.append(at >= flt.since)
    if flt.until is not None:
        conds.append(at < flt.until)
    if flt.chat_id is not None:
        conds.append(model.chat_id == flt.chat_id)
    if flt.status is not None:
        conds.append(model.status == flt.status)
    if flt.source is not None:
        conds.append(model.source == flt.source)
    if after is not None:
        # (at, rank, id) < after при постоянном rank таблицы.
        if rank < after.rank:
            conds.append(at <= after.at)
        elif rank > after.rank:
            conds.append(at < after.at)
        else:
            conds.append(or_(at < after.at, and_(at == after.at, model.id < after.id)))
    return query.where(*conds) if conds else query


def feed_types(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return FEED_TYPES
    types = tuple(t.strip() for t in raw.split(",") if t.strip())
    unknown = [t for t in types if t not in FEED_TYPES]
    if unknown or not types:
        raise ValueError(f"unknown journal types: {unknown}")
    return tuple(t for t in FEED_TYPES if t in types)
