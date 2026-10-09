from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import Database
from app.db.models import UserUiPref


class UiPrefsRepo:
    """Настройки интерфейса админки: по ключу на пользователя, значение - JSON."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, user_id: int, key: str) -> dict[str, Any] | None:
        query = select(UserUiPref.data).where(UserUiPref.user_id == user_id, UserUiPref.key == key)
        async with self._db.sessions() as session:
            return await session.scalar(query)

    async def put(self, user_id: int, key: str, data: dict[str, Any]) -> None:
        stmt = pg_insert(UserUiPref).values(user_id=user_id, key=key, data=data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[UserUiPref.user_id, UserUiPref.key],
            set_={"data": stmt.excluded.data, "updated_at": func.now()},
        )
        async with self._db.sessions() as session, session.begin():
            await session.execute(stmt)
