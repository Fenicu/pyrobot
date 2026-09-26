import logging

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

    async def notify(self, level: Level, code: str, text: str) -> None:
        try:
            await self._log.notify(level, code, text)
            async with self._db.sessions() as session, session.begin():
                session.add(
                    NotificationRow(account_id=self._account_id, level=level, code=code, text=text)
                )
        except Exception:
            log.exception("notify failed: %s", code)

    async def recent(self, limit: int = 50) -> list[NotificationRow]:
        async with self._db.sessions() as session:
            rows = await session.scalars(
                select(NotificationRow)
                .where(NotificationRow.account_id == self._account_id)
                .order_by(NotificationRow.id.desc())
                .limit(limit)
            )
            return list(rows)
