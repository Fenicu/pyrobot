from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.db.base import Database
from app.db.models import ServerSettingsRow
from app.engine.server_settings import ServerSettings
from app.engine.settings import SettingsConflict, patch_model, settings_diff

if TYPE_CHECKING:
    from app.db.audit import Actor, AuditLog

log = logging.getLogger(__name__)


class ServerSettingsRepo:
    """Хранилище настроек сервера (раздел 5.6 спеки)."""

    def __init__(self, db: Database, audit: AuditLog) -> None:
        self._db = db
        self._audit = audit
        self._current: ServerSettings = ServerSettings()
        self._version: int = 0

    @property
    def current(self) -> ServerSettings:
        return self._current

    @property
    def version(self) -> int:
        return self._version

    async def load(self) -> tuple[ServerSettings, int]:
        async with self._db.sessions() as session:
            row = await session.get(ServerSettingsRow, 1)
            if row is not None:
                self._current = ServerSettings.model_validate(row.data)
                self._version = row.version
            else:
                self._current = ServerSettings()
                self._version = 0
        return self._current, self._version

    async def update(
        self,
        changes: Mapping[str, Any],
        *,
        version: int,
        actor: Actor,
    ) -> tuple[ServerSettings, int, dict[str, list[Any]]]:
        async with self._db.sessions() as session, session.begin():
            row = await session.scalar(
                select(ServerSettingsRow).where(ServerSettingsRow.id == 1).with_for_update()
            )
            if row is None:
                raise RuntimeError("server_settings row 1 not found")
            if row.version != version:
                raise SettingsConflict(f"version {row.version} != {version}")

            new_settings = patch_model(ServerSettings, row.data, changes)
            new_data = new_settings.model_dump(mode="json")
            diff = settings_diff(row.data, new_data)
            if not diff:
                return self._current, self._version, {}

            new_version = row.version + 1
            row.version = new_version
            row.data = new_data
            row.updated_at = func.now()

            await self._audit.write(
                actor,
                "server_settings_changed",
                target_type="server",
                target_id=1,
                details={"changes": diff},
                session=session,
            )

        self._current = new_settings
        self._version = new_version
        return new_settings, new_version, diff
