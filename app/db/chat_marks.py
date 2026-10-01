from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import Database
from app.db.models import TgChatMark
from app.engine.fence import Fence

if TYPE_CHECKING:
    from app.engine.transport.history import Reader


class ChatMarks:
    """Отметки сверки истории аккаунта (раздел 4.3 спеки): до какого `msg_id` включительно
    чтение (чат, отправитель) сверено с журналом. Запись отметки только увеличивает её
    (`GREATEST`), поэтому не ограждена строкой аккаунта (раздел 4.2): опоздавшая запись прежнего
    держателя аренды её не откатывает, — проверяется только местный срок. Удаление ограждено,
    как остальные пишущие транзакции аккаунта."""

    def __init__(self, db: Database, account_id: int, fence: Fence | None = None) -> None:
        self._db = db
        self._account_id = account_id
        self._fence = fence

    async def get(self, reader: Reader) -> int | None:
        chat_id, from_id = reader
        query = select(TgChatMark.msg_id).where(
            TgChatMark.account_id == self._account_id,
            TgChatMark.chat_id == chat_id,
            TgChatMark.from_id == from_id,
        )
        async with self._db.sessions() as session:
            return await session.scalar(query)

    async def advance(self, reader: Reader, msg_id: int) -> None:
        if self._fence is not None:
            self._fence.check()
        chat_id, from_id = reader
        stmt = pg_insert(TgChatMark).values(
            account_id=self._account_id, chat_id=chat_id, from_id=from_id, msg_id=msg_id
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[TgChatMark.account_id, TgChatMark.chat_id, TgChatMark.from_id],
            set_={
                "msg_id": func.greatest(TgChatMark.msg_id, stmt.excluded.msg_id),
                "updated_at": func.now(),
            },
        )
        async with self._db.sessions() as session, session.begin():
            await session.execute(stmt)

    async def prune(self, keep: set[Reader]) -> int:
        """Удалить отметки чтений не из `keep` (их больше нет в настройках): чат, добавленный
        снова, читается с последнего сообщения. Сколько удалено."""
        stmt = delete(TgChatMark).where(TgChatMark.account_id == self._account_id)
        if keep:
            stmt = stmt.where(tuple_(TgChatMark.chat_id, TgChatMark.from_id).not_in(list(keep)))
        async with self._db.sessions() as session, session.begin():
            if self._fence is not None:
                await self._fence.guard(session)
            return len(list(await session.scalars(stmt.returning(TgChatMark.chat_id))))
