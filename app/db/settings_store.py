import asyncio

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import Database
from app.db.models import SettingsHistory, SettingsRow
from app.engine.settings import Settings, SettingsChange, SettingsConflict


class DbSettingsStore:
    def __init__(self, db: Database, account_id: int) -> None:
        self._db = db
        self._account_id = account_id
        self._settings = Settings()
        self._version = 0
        self._lock = asyncio.Lock()

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
    ) -> Settings:
        async with self._lock:
            if expected_version is not None and expected_version != self._version:
                raise SettingsConflict(f"version {self._version} != {expected_version}")
            changed = change(self._settings.model_copy(deep=True))
            new = Settings.model_validate(changed.model_dump())
            version = self._version + 1
            data = new.model_dump(mode="json")
            async with self._db.sessions() as session, session.begin():
                stmt = (
                    pg_insert(SettingsRow)
                    .values(account_id=self._account_id, version=version, data=data)
                    .on_conflict_do_update(
                        index_elements=[SettingsRow.account_id],
                        set_={"version": version, "data": data},
                        where=SettingsRow.version == self._version,
                    )
                    .returning(SettingsRow.version)
                )
                if await session.scalar(stmt) is None:
                    raise SettingsConflict("settings changed concurrently")
                session.add(
                    SettingsHistory(
                        account_id=self._account_id,
                        version=version,
                        data=data,
                        changed_by=changed_by,
                    )
                )
            self._settings, self._version = new, version
            return new
