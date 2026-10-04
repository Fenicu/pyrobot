from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from sqlalchemy import delete, func, select, update

from app.db.base import Database
from app.db.models import Account, AuthSession, User

Role = Literal["owner", "user"]


@dataclass(frozen=True)
class UserInfo:
    id: int
    login: str
    role: Role
    max_accounts: int
    created_at: datetime
    last_login_at: datetime | None
    disabled_at: datetime | None
    disabled_reason: str | None
    invited_by: int | None
    deleting_at: datetime | None

    @property
    def active(self) -> bool:
        return self.disabled_at is None and self.deleting_at is None


def _to_info(u: User) -> UserInfo:
    return UserInfo(
        id=u.id,
        login=u.login,
        role=cast(Role, u.role),
        max_accounts=u.max_accounts,
        created_at=u.created_at,
        last_login_at=u.last_login_at,
        disabled_at=u.disabled_at,
        disabled_reason=u.disabled_reason,
        invited_by=u.invited_by,
        deleting_at=u.deleting_at,
    )


class LoginTaken(Exception):
    """Логин уже занят."""


class LastOwner(Exception):
    """Действие оставит сервер без активного владельца."""


class UserRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, user_id: int) -> UserInfo | None:
        async with self._db.sessions() as session:
            row = await session.get(User, user_id)
            return _to_info(row) if row is not None else None

    async def by_login(self, login: str) -> UserInfo | None:
        async with self._db.sessions() as session:
            row = await session.scalar(select(User).where(User.login == login))
            return _to_info(row) if row is not None else None

    async def all(self) -> list[UserInfo]:
        async with self._db.sessions() as session:
            rows = await session.scalars(select(User).order_by(User.id))
            return [_to_info(r) for r in rows]

    async def promote(self, login: str) -> UserInfo:
        async with self._db.sessions() as session, session.begin():
            row = await session.scalar(select(User).where(User.login == login).with_for_update())
            if row is None:
                raise KeyError(login)
            row.role = "owner"
            return _to_info(row)

    async def set_limit(self, user_id: int, max_accounts: int) -> UserInfo:
        async with self._db.sessions() as session, session.begin():
            row = await session.get(User, user_id, with_for_update=True)
            if row is None:
                raise KeyError(user_id)
            row.max_accounts = max_accounts
            return _to_info(row)

    async def disable(self, user_id: int, reason: str | None) -> list[int]:
        async with self._db.sessions() as session, session.begin():
            active_owners = list(
                await session.scalars(
                    select(User)
                    .where(
                        User.role == "owner",
                        User.disabled_at.is_(None),
                        User.deleting_at.is_(None),
                    )
                    .order_by(User.id)
                    .with_for_update()
                )
            )
            row = await session.get(User, user_id, with_for_update=True)
            if row is None:
                raise KeyError(user_id)

            if row.role == "owner" and row.disabled_at is None and row.deleting_at is None:
                if len(active_owners) <= 1:
                    raise LastOwner("cannot disable the last active owner")

            row.disabled_at = func.now()
            row.disabled_reason = reason
            await session.execute(delete(AuthSession).where(AuthSession.admin_user_id == user_id))
            disabled_accounts = list(
                await session.scalars(
                    update(Account)
                    .where(Account.owner_id == user_id, Account.status != "deleting")
                    .values(
                        status="disabled",
                        status_reason="user_disabled",
                        updated_at=func.now(),
                    )
                    .returning(Account.id)
                )
            )
            return cast(Sequence[int], disabled_accounts)  # type: ignore[return-value]

    async def enable(self, user_id: int) -> UserInfo:
        async with self._db.sessions() as session, session.begin():
            row = await session.get(User, user_id, with_for_update=True)
            if row is None:
                raise KeyError(user_id)
            row.disabled_at = None
            row.disabled_reason = None
            return _to_info(row)

    async def touch_login(self, user_id: int) -> None:
        async with self._db.sessions() as session, session.begin():
            await session.execute(
                update(User).where(User.id == user_id).values(last_login_at=func.now())
            )

    async def owners_active(self) -> int:
        async with self._db.sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.role == "owner",
                    User.disabled_at.is_(None),
                    User.deleting_at.is_(None),
                )
            )
            return int(count or 0)

    async def mark_deleting(self, user_id: int) -> list[int]:
        """Помечает пользователя на удаление или сразу удаляет, если аккаунтов нет."""
        async with self._db.sessions() as session, session.begin():
            active_owners = list(
                await session.scalars(
                    select(User)
                    .where(
                        User.role == "owner",
                        User.disabled_at.is_(None),
                        User.deleting_at.is_(None),
                    )
                    .order_by(User.id)
                    .with_for_update()
                )
            )
            row = await session.get(User, user_id, with_for_update=True)
            if row is None:
                raise KeyError(user_id)

            if row.role == "owner" and row.disabled_at is None and row.deleting_at is None:
                if len(active_owners) <= 1:
                    raise LastOwner("cannot delete the last active owner")
            elif row.role == "owner" and len(active_owners) == 0:
                raise LastOwner("cannot delete owner when no active owners remain")

            now = func.now()
            if row.disabled_at is None:
                row.disabled_at = now
            row.deleting_at = now
            await session.execute(delete(AuthSession).where(AuthSession.admin_user_id == user_id))

            await session.execute(
                update(Account)
                .where(Account.owner_id == user_id, Account.status != "deleting")
                .values(
                    status="deleting",
                    updated_at=now,
                )
            )

            account_ids = list(
                await session.scalars(
                    select(Account.id).where(Account.owner_id == user_id).order_by(Account.id)
                )
            )
            if not account_ids:
                await session.execute(delete(User).where(User.id == user_id))
                return []
            return list(account_ids)

    async def finish_deleting(self, user_id: int) -> bool:
        """Удаляет строку пользователя, если он помечен на удаление и не осталось аккаунтов."""
        async with self._db.sessions() as session, session.begin():
            has_accounts = select(1).where(Account.owner_id == user_id).exists()
            deleted = await session.scalar(
                delete(User)
                .where(
                    User.id == user_id,
                    User.deleting_at.is_not(None),
                    ~has_accounts,
                )
                .returning(User.id)
            )
            return deleted is not None
