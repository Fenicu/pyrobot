from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.db.base import Database
from app.db.models import SettingsHistory
from app.engine.settings import Settings, settings_diff


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
