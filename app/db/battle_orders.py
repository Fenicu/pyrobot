from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text

from app.db.base import Database
from app.db.models import BattleOrderRow
from app.engine.battle_orders import BattleOrder


def lock_key(owner_id: int, battle_at: datetime) -> str:
    return f"battle_orders:{owner_id}:{battle_at.astimezone(UTC).isoformat()}"


class BattleOrderRepo:
    """Приказы штаба битвы: набор на одну битву заменяется целиком."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def active(self, owner_id: int, since: datetime) -> tuple[BattleOrder, ...]:
        query = (
            select(BattleOrderRow.battle_at, BattleOrderRow.scope, BattleOrderRow.target)
            .where(BattleOrderRow.owner_id == owner_id, BattleOrderRow.battle_at >= since)
            .order_by(BattleOrderRow.battle_at, BattleOrderRow.scope)
        )
        async with self._db.sessions() as session:
            return tuple(
                BattleOrder(at, scope, target)
                for at, scope, target in await session.execute(query)
            )

    async def replace(
        self, owner_id: int, battle_at: datetime, orders: Mapping[str, str], by: str
    ) -> None:
        """Пустой набор - отмена. Замены одной пары (владелец, битва) идут строго по очереди:
        без блокировки две замены пустой битвы дали бы объединение наборов или конфликт ключа."""
        async with self._db.sessions() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": lock_key(owner_id, battle_at)},
            )
            await session.execute(
                delete(BattleOrderRow).where(
                    BattleOrderRow.owner_id == owner_id, BattleOrderRow.battle_at == battle_at
                )
            )
            await session.execute(
                delete(BattleOrderRow).where(
                    BattleOrderRow.owner_id == owner_id,
                    BattleOrderRow.battle_at < func.now() - text("interval '1 day'"),
                )
            )
            session.add_all(
                BattleOrderRow(
                    owner_id=owner_id,
                    battle_at=battle_at,
                    scope=scope,
                    target=target,
                    created_by=by,
                )
                for scope, target in orders.items()
            )
