from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import Database
from app.db.models import LedgerRow, MessageRow, MetricRow, StateSnapshot, UnrecognizedRow
from app.engine.events import Event, Unrecognized
from app.engine.gametime import tasks_day
from app.engine.state.ledger import Effect, numbered
from app.engine.types import Button, IncomingMessage


def _restored(row: MessageRow) -> IncomingMessage:
    markup = row.markup or {}
    inline = tuple(
        Button(text=b[0], row=b[1], col=b[2], data=b[3], url=b[4], switch=b[5])
        for b in markup.get("inline", [])
    )
    reply = tuple(tuple(r) for r in markup.get("reply", []))
    return IncomingMessage(
        chat_id=row.chat_id,
        msg_id=row.msg_id,
        revision=row.revision,
        kind="edit" if row.kind == "edit" else "new",
        date=row.date,
        received_at=row.received_at,
        text=row.text,
        inline=inline,
        reply_kb=reply,
        from_id=row.from_id,
        outgoing=row.outgoing,
        recovered=row.recovered,
    )


class DbJournal:
    def __init__(self, db: Database, account_id: int) -> None:
        self._db = db
        self._account_id = account_id

    async def load_state(self) -> tuple[dict[str, Any], int]:
        async with self._db.sessions() as session:
            row = await session.scalar(
                select(StateSnapshot).where(StateSnapshot.account_id == self._account_id)
            )
        return (dict(row.state), row.version) if row else ({}, 0)

    async def messages_with_event(
        self, chat_id: int, kind: str, since: datetime
    ) -> list[IncomingMessage]:
        """Последние записанные ревизии сообщений чата, у которых ревизия не раньше `since` дала
        событие `kind` (тревоги ограбления, оставшиеся от прошлого процесса)."""
        base = (MessageRow.account_id == self._account_id, MessageRow.chat_id == chat_id)
        marked = (
            select(MessageRow.msg_id)
            .where(*base, MessageRow.date >= since, MessageRow.events.contains([{"kind": kind}]))
            .scalar_subquery()
        )
        query = (
            select(MessageRow)
            .where(*base, MessageRow.msg_id.in_(marked))
            .order_by(MessageRow.msg_id, MessageRow.id.desc())
            .ext(distinct_on(MessageRow.msg_id))
        )
        async with self._db.sessions() as session:
            rows = await session.scalars(query)
            return [_restored(row) for row in rows]

    async def revisions(self, chat_id: int, msg_id: int) -> list[IncomingMessage]:
        """Все записанные правки сообщения в порядке журнала."""
        query = (
            select(MessageRow)
            .where(
                MessageRow.account_id == self._account_id,
                MessageRow.chat_id == chat_id,
                MessageRow.msg_id == msg_id,
            )
            .order_by(MessageRow.id)
        )
        async with self._db.sessions() as session:
            rows = await session.scalars(query)
            return [_restored(row) for row in rows]

    async def append(
        self,
        msg: IncomingMessage,
        events: Sequence[Event],
        new_state: dict[str, Any] | None,
        new_version: int,
        metrics: Mapping[str, float] | None = None,
        effects: Sequence[Effect] = (),
    ) -> int | None:
        async with self._db.sessions() as session, session.begin():
            stmt = (
                pg_insert(MessageRow)
                .values(
                    account_id=self._account_id,
                    chat_id=msg.chat_id,
                    msg_id=msg.msg_id,
                    revision=msg.revision,
                    content_hash=msg.content_hash(),
                    kind=msg.kind,
                    date=msg.date,
                    received_at=msg.received_at,
                    recovered=msg.recovered,
                    outgoing=msg.outgoing,
                    from_id=msg.from_id,
                    text=msg.text,
                    markup=msg.markup_json(),
                    events=[e.to_json() for e in events],
                )
                .on_conflict_do_nothing(
                    index_elements=["account_id", "chat_id", "msg_id", "revision", "content_hash"]
                )
                .returning(MessageRow.id)
            )
            journal_id = await session.scalar(stmt)
            if journal_id is None:
                return None
            if new_state is not None:
                snap = (
                    pg_insert(StateSnapshot)
                    .values(account_id=self._account_id, version=new_version, state=new_state)
                    .on_conflict_do_update(
                        index_elements=[StateSnapshot.account_id],
                        set_={"version": new_version, "state": new_state},
                    )
                )
                await session.execute(snap)
            if metrics:
                session.add_all(
                    MetricRow(account_id=self._account_id, ts=msg.date, key=key, value=value)
                    for key, value in metrics.items()
                )
            session.add_all(
                UnrecognizedRow(
                    account_id=self._account_id,
                    message_id=journal_id,
                    chat_id=msg.chat_id,
                    msg_id=msg.msg_id,
                    first_line=event.first_line,
                )
                for event in events
                if isinstance(event, Unrecognized)
            )
            if effects:
                await session.execute(self._ledger(msg, effects))
            return int(journal_id)

    def _ledger(self, msg: IncomingMessage, effects: Sequence[Effect]) -> Any:
        """Эффекты — после вставки ревизии, в той же транзакции: повтор после сбоя фиксации
        упирается в ревизию и их не задваивает; ключ эффекта — ключ ряда сообщения (с хешем
        содержимого) и номер, уже есть — «уже записано». Постоянный ключ итога (`outcome_key`,
        частичный уникальный индекс) уже есть — тоже «уже записано»: отчёт той же битвы из
        другого сообщения второго ряда не даёт, сколько бы времени ни прошло."""
        content_hash = msg.content_hash()
        rows = []
        for effect, seq in numbered(effects):
            at = effect.at or msg.date
            rows.append(
                {
                    "account_id": self._account_id,
                    "at": at,
                    "day": tasks_day(at),
                    "recorded_at": msg.received_at,
                    "kind": effect.kind,
                    "amounts": effect.amounts,
                    "items": effect.items,
                    "chat_id": msg.chat_id,
                    "msg_id": msg.msg_id,
                    "revision": msg.revision,
                    "content_hash": content_hash,
                    "seq": seq,
                    "outcome_key": effect.key,
                }
            )
        # Без цели конфликта: пропуск по любому уникальному ключу — и эффекта, и итога.
        return pg_insert(LedgerRow).values(rows).on_conflict_do_nothing()
