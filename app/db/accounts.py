from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import ValidationError
from sqlalchemy import ScalarSelect, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.base import Database
from app.db.models import (
    Account,
    ActionRow,
    AdminUser,
    DecisionRow,
    LedgerRow,
    MessageRow,
    MetricRow,
    MetroRunRow,
    NotificationRow,
    ScenarioRunRow,
    SettingsHistory,
    SettingsRow,
    StateSnapshot,
    TgChatMark,
    TgPeer,
    TgSession,
)
from app.engine.settings import EngineSection, Settings
from app.engine.state.model import company_of, team_tag_of
from app.engine.tg_auth import TgUserTaken

AccountStatus = Literal["enabled", "disabled", "error", "deleting"]

# Число `enabled` меняют создание и включение: они сверяют его с ёмкостью под этой блокировкой
# транзакции, иначе два запроса разом заняли бы одно последнее место.
_ENABLED_LOCK_KEY = 0x7079726F616363


class NameTaken(Exception):
    """У владельца уже есть аккаунт с таким именем."""


class CapacityReached(Exception):
    """Включённых аккаунтов стало бы больше ёмкости."""


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


@dataclass(frozen=True)
class AccountOverview:
    """Аккаунт в списке учётки: режим, пауза и kill из настроек в базе, последнее действие и
    непрочитанные уведомления `warn` и `error`; компания и тег команды — из последнего снимка
    состояния (None — снимка нет или поле не наблюдалось)."""

    account: AccountInfo
    engine: EngineSection
    last_action_at: datetime | None
    unread_warn: int
    unread_error: int
    company: str | None
    team_tag: str | None


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


# Чистка удаляемого аккаунта: журнал — в порядке `DbRetention` (нераспознанные уходят каскадом с
# сообщениями), затем сессия Telegram, пиры, отметки сверки, настройки, их история и снимок
# состояния.
_PURGED: tuple[Any, ...] = (
    MessageRow,
    ActionRow,
    ScenarioRunRow,
    NotificationRow,
    DecisionRow,
    MetricRow,
    MetroRunRow,
    LedgerRow,
    TgSession,
    TgPeer,
    TgChatMark,
    SettingsRow,
    SettingsHistory,
    StateSnapshot,
)


def engine_section(data: dict[str, Any] | None) -> EngineSection:
    """Секция движка из настроек в базе. Не читается — значения по умолчанию: список аккаунтов,
    статус и состояние без движка отдаются и у аккаунта, упавшего на старте из-за настроек."""
    try:
        return EngineSection.model_validate((data or {}).get("engine", {}))
    except ValidationError:
        return EngineSection()


def _unread(level: str) -> ScalarSelect[int]:
    return (
        select(func.count())
        .where(
            NotificationRow.account_id == Account.id,
            NotificationRow.level == level,
            NotificationRow.read.is_(False),
        )
        .scalar_subquery()
    )


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

    async def overview(self, owner_id: int) -> list[AccountOverview]:
        """Аккаунты учётки для списка — одним запросом: настройки, последнее действие и
        непрочитанные предупреждения и ошибки каждого."""
        last_action = (
            select(func.max(ActionRow.created_at))
            .where(ActionRow.account_id == Account.id)
            .scalar_subquery()
        )
        stmt = (
            select(
                Account,
                SettingsRow.data,
                last_action,
                _unread("warn"),
                _unread("error"),
                StateSnapshot.state,
            )
            .outerjoin(SettingsRow, SettingsRow.account_id == Account.id)
            .outerjoin(StateSnapshot, StateSnapshot.account_id == Account.id)
            .where(Account.owner_id == owner_id)
            .order_by(Account.id)
        )
        async with self._db.sessions() as session:
            rows = (await session.execute(stmt)).all()
        return [
            AccountOverview(
                _info(row),
                engine_section(data),
                last,
                int(warn),
                int(error),
                company_of(state or {}),
                team_tag_of(state or {}),
            )
            for row, data, last, warn, error, state in rows
        ]

    async def has_tg_session(self, account_id: int) -> bool:
        async with self._db.sessions() as session:
            found = await session.scalar(
                select(TgSession.account_id).where(TgSession.account_id == account_id)
            )
        return found is not None

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

    async def purge(self, account_id: int, *, batch: int = 5000) -> None:
        """Данные удаляемого аккаунта и его строка (чистка удаления, раздел 4.2 спеки). Строки
        с ключом `id` удаляются пачками по `batch`, каждая своей транзакцией: длинный DELETE
        держал бы блокировки; у таблиц с ключом по аккаунту строк на аккаунт единицы — они
        удаляются разом. Прерванная чистка продолжается повтором; аккаунта уже нет — ничего не
        делается, аккаунт не `deleting` — ValueError."""
        account = await self.get(account_id)
        if account is None:
            return
        if account.status != "deleting":
            raise ValueError(f"account {account_id} is not deleting")
        for model in _PURGED:
            deleted = batch
            while deleted >= batch:
                deleted = await self._delete_rows(model, account_id, batch)
        async with self._db.sessions() as session, session.begin():
            await session.execute(
                delete(Account).where(Account.id == account_id, Account.status == "deleting")
            )

    async def _delete_rows(self, model: Any, account_id: int, batch: int) -> int:
        """Пачка строк аккаунта; сколько удалено. Без пачек (0) — у таблиц, чей ключ включает
        аккаунт."""
        own = model.account_id == account_id
        async with self._db.sessions() as session, session.begin():
            if "account_id" in model.__table__.primary_key.columns:
                await session.execute(delete(model).where(own))
                return 0
            ids = select(model.id).where(own).limit(batch).scalar_subquery()
            result = await session.execute(
                delete(model).where(own, model.id.in_(ids)).returning(model.id)
            )
            return len(result.all())

    async def bind_telegram(self, account_id: int, tg_user_id: int) -> int:
        """Привязка на всю жизнь: непривязанный аккаунт получает `tg_user_id`, уже привязанный не
        меняется. Ответ — привязка в базе после записи: у привязанного к другому — тот, другой.
        Пользователь, занятый другим аккаунтом, — `TgUserTaken`; аккаунта нет — KeyError."""
        try:
            async with self._db.sessions() as session, session.begin():
                bound: int | None = await session.scalar(
                    update(Account)
                    .where(Account.id == account_id, Account.tg_user_id.is_(None))
                    .values(tg_user_id=tg_user_id, updated_at=func.now())
                    .returning(Account.tg_user_id)
                )
                if bound is None:
                    bound = await session.scalar(
                        select(Account.tg_user_id).where(Account.id == account_id)
                    )
        except IntegrityError as exc:
            if _violated(exc) == "uq_accounts_tg_user_id":
                raise TgUserTaken(tg_user_id) from exc
            raise
        if bound is None:
            raise KeyError(account_id)
        return bound

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
