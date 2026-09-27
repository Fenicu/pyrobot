import asyncio

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Base
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
        "metro_runs",
    } <= await _tables()
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
    assert await _tables() <= {"alembic_version"}


FK_INDEXES = {
    "ix_unrecognized_message_id",
    "ix_scenario_runs_decision_id",
    "ix_metro_runs_scenario_run_id",
}


async def test_models_match_migrations() -> None:
    await asyncio.to_thread(command.upgrade, _cfg(), "head")
    engine = create_async_engine(MIGTEST_DB_URL)
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda c: compare_metadata(
                MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata
            )
        )
        rows = await conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        )
        indexes = {r[0] for r in rows}
    await engine.dispose()
    assert diff == []
    # Удаление по внешнему ключу (ретеншн сообщений, решений, запусков) без индекса — seq scan.
    assert FK_INDEXES <= indexes
