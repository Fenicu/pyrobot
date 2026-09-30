import asyncio
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Database
from app.db.models import Account, SettingsHistory, SettingsRow
from app.engine.fence import Fence
from app.engine.settings import Settings, SettingsChange, SettingsConflict

log = logging.getLogger(__name__)


class LeaseHeld(Exception):
    """Аренда аккаунта занята: настройки пишет её держатель (движок), прямой записи нет."""


async def _write(
    session: AsyncSession,
    account_id: int,
    version: int,
    data: dict[str, Any],
    *,
    previous: int,
    changed_by: str,
) -> None:
    """Новая версия настроек и её строка истории; версия в базе уже не `previous` —
    `SettingsConflict`."""
    stmt = (
        pg_insert(SettingsRow)
        .values(account_id=account_id, version=version, data=data)
        .on_conflict_do_update(
            index_elements=[SettingsRow.account_id],
            set_={"version": version, "data": data},
            where=SettingsRow.version == previous,
        )
        .returning(SettingsRow.version)
    )
    if await session.scalar(stmt) is None:
        raise SettingsConflict("settings changed concurrently")
    session.add(
        SettingsHistory(account_id=account_id, version=version, data=data, changed_by=changed_by)
    )


async def direct_update(
    db: Database,
    account_id: int,
    change: SettingsChange,
    *,
    changed_by: str,
    expected_version: int | None,
) -> tuple[Settings, int]:
    """Запись настроек аккаунта без движка (раздел 4.4 спеки) — одной транзакцией, пока строка
    аккаунта под FOR SHARE: захват аренды (UPDATE accounts) ждёт её коммита, а движок читает
    настройки только после захвата — запись до него он видит. Аренда занята — `LeaseHeld`,
    версия не та — `SettingsConflict`, аккаунта нет — KeyError."""
    async with db.sessions() as session, session.begin():
        lease = (
            await session.execute(
                select(Account.lease_holder, Account.lease_expires_at, func.now())
                .where(Account.id == account_id)
                .with_for_update(read=True)
            )
        ).one_or_none()
        if lease is None:
            raise KeyError(account_id)
        holder, expires, now = lease
        # Свободна — как при захвате: держателя нет или срок прошёл.
        if holder is not None and not (expires is not None and expires < now):
            raise LeaseHeld(account_id)
        row = await session.scalar(
            select(SettingsRow).where(SettingsRow.account_id == account_id).with_for_update()
        )
        current = Settings.model_validate(row.data) if row is not None else Settings()
        previous = row.version if row is not None else 0
        if expected_version is not None and expected_version != previous:
            raise SettingsConflict(f"version {previous} != {expected_version}")
        new = Settings.model_validate(change(current).model_dump())
        version = previous + 1
        await _write(
            session,
            account_id,
            version,
            new.model_dump(mode="json"),
            previous=previous,
            changed_by=changed_by,
        )
    return new, version


class DbSettingsStore:
    def __init__(self, db: Database, account_id: int, *, fence: Fence | None = None) -> None:
        self._db = db
        self._account_id = account_id
        self._fence = fence
        self._settings = Settings()
        self._version = 0
        self._lock = asyncio.Lock()
        # Вызываются после сохранения новой версии (поток SSE).
        self.listeners: list[Callable[[Settings, int], None]] = []

    @property
    def current(self) -> Settings:
        return self._settings

    @property
    def version(self) -> int:
        return self._version

    async def load(self) -> None:
        async with self._lock:
            async with self._db.sessions() as session:
                row = await session.scalar(
                    select(SettingsRow).where(SettingsRow.account_id == self._account_id)
                )
            if row is not None:
                self._settings = Settings.model_validate(row.data)
                self._version = row.version

    async def update(
        self, change: SettingsChange, *, changed_by: str, expected_version: int | None = None
    ) -> tuple[Settings, int]:
        async with self._lock:
            if expected_version is not None and expected_version != self._version:
                raise SettingsConflict(f"version {self._version} != {expected_version}")
            changed = change(self._settings.model_copy(deep=True))
            new = Settings.model_validate(changed.model_dump())
            version = self._version + 1
            data = new.model_dump(mode="json")
            async with self._db.sessions() as session, session.begin():
                if self._fence is not None:
                    await self._fence.guard(session)
                await _write(
                    session,
                    self._account_id,
                    version,
                    data,
                    previous=self._version,
                    changed_by=changed_by,
                )
            self._settings, self._version = new, version
        for listener in self.listeners:
            try:
                listener(new, version)
            except Exception:
                log.exception("settings listener failed")
        return new, version
