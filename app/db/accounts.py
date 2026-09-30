from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.base import Database
from app.db.models import Account, AdminUser, SettingsRow
from app.engine.settings import Settings

AccountStatus = Literal["enabled", "disabled", "error", "deleting"]

# Число `enabled` меняют создание и включение: они сверяют его с ёмкостью под этой блокировкой
# транзакции, иначе два запроса разом заняли бы одно последнее место.
_ENABLED_LOCK_KEY = 0x7079726F616363


class NameTaken(Exception):
    """У владельца уже есть аккаунт с таким именем."""


class CapacityReached(Exception):
    """Включённых аккаунтов стало бы больше ёмкости."""


class TgUserTaken(Exception):
    """Пользователь Telegram уже привязан к другому аккаунту."""


class AccountDeleting(Exception):
    """Аккаунт удаляется: `deleting` — конечный статус, правка отклонена."""


@dataclass(frozen=True)
class AccountInfo:
    id: int
    owner_id: int | None
    name: str
    status: AccountStatus
    status_reason: str | None
    tg_user_id: int | None
    engine_generation: int


def _info(row: Account) -> AccountInfo:
    return AccountInfo(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        status=cast(AccountStatus, row.status),
        status_reason=row.status_reason,
        tg_user_id=row.tg_user_id,
        engine_generation=row.engine_generation,
    )


# `deleting` конечный: записи, которые его сняли бы или перезапустили движок, его не затрагивают.
_NOT_DELETING = Account.status != "deleting"


def _violated(exc: IntegrityError) -> str | None:
    """Имя нарушенного ограничения или индекса (у asyncpg — на исходном исключении)."""
    return getattr(exc.orig.__cause__, "constraint_name", None) if exc.orig else None


async def _lock_enabled(session: AsyncSession) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ENABLED_LOCK_KEY})


async def _check_capacity(session: AsyncSession, capacity: int) -> None:
    """Ещё один `enabled` должен поместиться; вызывается под `_lock_enabled`."""
    enabled = await session.scalar(
        select(func.count()).select_from(Account).where(Account.status == "enabled")
    )
    if (enabled or 0) + 1 > capacity:
        raise CapacityReached


class AccountRepo:
    """Реестр аккаунтов. Каждая запись обновляет `updated_at`."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, account_id: int) -> AccountInfo | None:
        async with self._db.sessions() as session:
            row = await session.get(Account, account_id)
        return _info(row) if row is not None else None

    async def owned(self, owner_id: int) -> list[AccountInfo]:
        async with self._db.sessions() as session:
            rows = await session.scalars(
                select(Account).where(Account.owner_id == owner_id).order_by(Account.id)
            )
            return [_info(r) for r in rows]

    async def with_status(self, *statuses: AccountStatus) -> list[AccountInfo]:
        async with self._db.sessions() as session:
            rows = await session.scalars(
                select(Account).where(Account.status.in_(statuses)).order_by(Account.id)
            )
            return [_info(r) for r in rows]

    async def create(self, owner_id: int, name: str, *, capacity: int) -> AccountInfo:
        """Новый аккаунт `enabled` с настройками по умолчанию — одной транзакцией."""
        async with self._db.sessions() as session, session.begin():
            await _lock_enabled(session)
            await _check_capacity(session, capacity)
            row = await session.scalar(
                pg_insert(Account)
                .values(owner_id=owner_id, name=name, status="enabled")
                .on_conflict_do_nothing(constraint="uq_accounts_owner_name")
                .returning(Account)
            )
            if row is None:
                raise NameTaken(name)
            session.add(
                SettingsRow(account_id=row.id, version=1, data=Settings().model_dump(mode="json"))
            )
            return _info(row)

    async def update(
        self,
        account_id: int,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        capacity: int,
    ) -> AccountInfo:
        """Переименование и включение (`enabled=True` очищает причину) или выключение.
        KeyError — нет такого аккаунта; AccountDeleting — он удаляется, и ничего не меняется."""
        try:
            async with self._db.sessions() as session, session.begin():
                if enabled:
                    await _lock_enabled(session)
                row = await session.scalar(
                    select(Account).where(Account.id == account_id).with_for_update()
                )
                if row is None:
                    raise KeyError(account_id)
                if row.status == "deleting":
                    raise AccountDeleting(account_id)
                if enabled and row.status != "enabled":
                    await _check_capacity(session, capacity)
                if name is None and enabled is None:
                    return _info(row)
                if name is not None:
                    row.name = name
                if enabled is not None:
                    row.status = "enabled" if enabled else "disabled"
                    if enabled:
                        row.status_reason = None
                row.updated_at = func.now()
                await session.flush()
                return _info(row)
        except IntegrityError as exc:
            if _violated(exc) == "uq_accounts_owner_name":
                raise NameTaken(name) from exc
            raise

    async def set_status(self, account_id: int, status: AccountStatus, reason: str | None) -> None:
        """У удаляемого аккаунта (`deleting`) статус не меняется: чистка должна дойти до конца."""
        await self._write(account_id, _NOT_DELETING, status=status, status_reason=reason)

    async def restart(self, account_id: int) -> None:
        await self._write(
            account_id, _NOT_DELETING, engine_generation=Account.engine_generation + 1
        )

    async def mark_deleting(self, account_id: int) -> None:
        await self._write(account_id, status="deleting")

    async def bind_telegram(self, account_id: int, tg_user_id: int) -> None:
        """Привязка на всю жизнь: уже привязанный аккаунт не меняется; пользователь, занятый
        другим аккаунтом, — `TgUserTaken`."""
        try:
            await self._write(account_id, Account.tg_user_id.is_(None), tg_user_id=tg_user_id)
        except IntegrityError as exc:
            if _violated(exc) == "uq_accounts_tg_user_id":
                raise TgUserTaken(tg_user_id) from exc
            raise

    async def adopt_orphans(self) -> int:
        """Аккаунты без владельца достаются первой (по `id`) учётке; сколько аккаунтов получили
        владельца."""
        first = select(func.min(AdminUser.id)).scalar_subquery()
        async with self._db.sessions() as session, session.begin():
            adopted = await session.scalars(
                update(Account)
                .where(Account.owner_id.is_(None), first.is_not(None))
                .values(owner_id=first, updated_at=func.now())
                .returning(Account.id)
            )
            return len(adopted.all())

    async def _write(self, account_id: int, *where: ColumnElement[bool], **values: Any) -> None:
        stmt = (
            update(Account)
            .where(Account.id == account_id, *where)
            .values(updated_at=func.now(), **values)
        )
        async with self._db.sessions() as session, session.begin():
            await session.execute(stmt)
