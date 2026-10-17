from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Text, cast, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, array

from app.db.accounts import AccountInfo, _info
from app.db.base import Database
from app.db.models import Account, SettingsRow, StateSnapshot
from app.engine.settings import Settings
from app.engine.state.model import SCHEMA_VERSION, CharacterState, load_state

# Тяжёлые ключи снимка обзору не нужны: `applied` держит ключи итогов за две недели.
_HEAVY = ("applied", "activity_stats", "gadgets", "trips")


@dataclass(frozen=True, slots=True)
class AccountInput:
    """Аккаунт учётки для экранов обзора: настройки (при сбое чтения - умолчания) и снимок
    состояния; `state` None - снимка нет или у него другая версия схемы."""

    info: AccountInfo
    settings: Settings
    state: CharacterState | None


def _settings(data: dict[str, Any] | None) -> Settings:
    try:
        return Settings.model_validate(data or {})
    except ValidationError:
        return Settings()


def _state(data: dict[str, Any] | None) -> CharacterState | None:
    if not data or data.get("schema_version") != SCHEMA_VERSION:
        return None
    return load_state(data)


class OverviewReads:
    """Чтения раздела «Обзор»: всё в границах аккаунтов одной учётки."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def account_inputs(self, owner_id: int) -> list[AccountInput]:
        """Аккаунты учётки (кроме удаляемых) одним запросом: настройки и снимок состояния без
        тяжёлых ключей."""
        light = StateSnapshot.state.op("-", return_type=JSONB)(cast(array(_HEAVY), ARRAY(Text)))
        stmt = (
            select(Account, SettingsRow.data, light)
            .outerjoin(SettingsRow, SettingsRow.account_id == Account.id)
            .outerjoin(StateSnapshot, StateSnapshot.account_id == Account.id)
            .where(Account.owner_id == owner_id, Account.status != "deleting")
            .order_by(Account.id)
        )
        async with self._db.sessions() as session:
            rows = (await session.execute(stmt)).all()
        return [
            AccountInput(_info(row), _settings(data), _state(state)) for row, data, state in rows
        ]
