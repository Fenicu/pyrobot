from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import Database
from app.db.models import MessageRow, StateSnapshot
from app.engine.events import Event
from app.engine.types import IncomingMessage


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

    async def append(
        self,
        msg: IncomingMessage,
        events: Sequence[Event],
        new_state: dict[str, Any] | None,
        new_version: int,
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
            return int(journal_id)
