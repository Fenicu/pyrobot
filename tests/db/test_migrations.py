import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import MIGTEST_DB_URL

pytestmark = pytest.mark.db


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", MIGTEST_DB_URL)
    return cfg


async def _tables() -> set[str]:
    engine = create_async_engine(MIGTEST_DB_URL)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        names = {r[0] for r in rows}
    await engine.dispose()
    return names


async def test_upgrade_and_downgrade() -> None:
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
    await asyncio.to_thread(command.upgrade, _cfg(), "head")
    assert {
        "accounts",
        "messages",
        "actions",
        "settings",
        "auth_sessions",
        "metrics",
        "unrecognized",
        "decisions",
        "scenario_runs",
    } <= await _tables()
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
    assert await _tables() <= {"alembic_version"}
