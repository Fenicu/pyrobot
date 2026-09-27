import logging
from collections.abc import Callable

from sqlalchemy import select

from app.db.base import Database
from app.db.models import NotificationRow
from app.engine.notify import Level, LogNotifier

log = logging.getLogger(__name__)


class DbNotifier:
    def __init__(self, db: Database, account_id: int) -> None:
        self._db = db
        self._account_id = account_id
        self._log = LogNotifier()
        # Вызываются после записи (поток SSE); исключение слушателя не мешает уведомлению.
        self.listeners: list[Callable[[NotificationRow], None]] = []

    async def notify(self, level: Level, code: str, text: str) -> None:
        try:
            await self._log.notify(level, code, text)
            row = NotificationRow(account_id=self._account_id, level=level, code=code, text=text)
            async with self._db.sessions() as session, session.begin():
                session.add(row)
        except Exception:
            log.exception("notify failed: %s", code)
            return
        for listener in self.listeners:
            try:
                listener(row)
            except Exception:
                log.exception("notification listener failed: %s", code)

    async def recent(self, limit: int = 50) -> list[NotificationRow]:
        async with self._db.sessions() as session:
            rows = await session.scalars(
                select(NotificationRow)
                .where(NotificationRow.account_id == self._account_id)
                .order_by(NotificationRow.id.desc())
                .limit(limit)
            )
            return list(rows)
