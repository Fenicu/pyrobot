from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import func, select

from app.db.base import Database
from app.db.models import Account, User
from app.db.users import Role


@dataclass(frozen=True)
class AdminUserRow:
    id: int
    login: str
    role: Role
    created_at: datetime
    last_login_at: datetime | None
    accounts: int
    max_accounts: int
    disabled: bool
    disabled_reason: str | None
    deleting: bool


class AdminReads:
    """Служебные выборки пользователей и аккаунтов для консоли владельца."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def users(self) -> list[AdminUserRow]:
        accounts_count = (
            select(func.count(Account.id))
            .where(Account.owner_id == User.id, Account.status != "deleting")
            .correlate(User)
            .scalar_subquery()
        )
        stmt = select(User, accounts_count.label("accounts_count")).order_by(User.id)
        async with self._db.sessions() as session:
            rows = (await session.execute(stmt)).all()
            return [
                AdminUserRow(
                    id=u.id,
                    login=u.login,
                    role=cast(Role, u.role),
                    created_at=u.created_at,
                    last_login_at=u.last_login_at,
                    accounts=int(cnt or 0),
                    max_accounts=u.max_accounts,
                    disabled=u.disabled_at is not None,
                    disabled_reason=u.disabled_reason,
                    deleting=u.deleting_at is not None,
                )
                for u, cnt in rows
            ]

    async def user(self, user_id: int) -> AdminUserRow | None:
        accounts_count = (
            select(func.count(Account.id))
            .where(Account.owner_id == User.id, Account.status != "deleting")
            .correlate(User)
            .scalar_subquery()
        )
        stmt = select(User, accounts_count.label("accounts_count")).where(User.id == user_id)
        async with self._db.sessions() as session:
            row = (await session.execute(stmt)).first()
            if row is None:
                return None
            u, cnt = row
            return AdminUserRow(
                id=u.id,
                login=u.login,
                role=cast(Role, u.role),
                created_at=u.created_at,
                last_login_at=u.last_login_at,
                accounts=int(cnt or 0),
                max_accounts=u.max_accounts,
                disabled=u.disabled_at is not None,
                disabled_reason=u.disabled_reason,
                deleting=u.deleting_at is not None,
            )
