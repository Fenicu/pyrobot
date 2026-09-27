import asyncio
from datetime import UTC, datetime, timedelta

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
    "ix_actions_scenario_run_id",
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


def _at(minute: int) -> datetime:
    return datetime(2026, 9, 27, 12, tzinfo=UTC) + timedelta(minutes=minute)


async def _exec(sql: str, **params: object) -> list[tuple[object, ...]]:
    engine = create_async_engine(MIGTEST_DB_URL)
    async with engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        rows = [tuple(r) for r in result.all()] if result.returns_rows else []
    await engine.dispose()
    return rows


async def _run(run_id: int, start: int, end: int | None, by: str | None = None) -> None:
    await _exec(
        "INSERT INTO scenario_runs (id, account_id, scenario, params, started_at, finished_at,"
        " status, reason, requested_by) VALUES (:id, 1, 'deed:job', '{}', :s, :f, :st, '', :by)",
        id=run_id,
        s=_at(start),
        f=_at(end) if end is not None else None,
        st="done" if end is not None else "running",
        by=by,
    )


async def _action(
    action_id: int,
    minute: int,
    source: str,
    *,
    key: str | None = None,
    cls: str = "action",
    status: str = "confirmed",
) -> None:
    await _exec(
        "INSERT INTO actions (id, account_id, created_at, source, kind, chat_id, payload,"
        " command_class, status, reason, idempotency_key, attempts)"
        " VALUES (:id, 1, :at, :src, 'send', 1, '{}', :cls, :st, '', :key, 0)",
        id=action_id,
        at=_at(minute),
        src=source,
        cls=cls,
        st=status,
        key=key,
    )


async def test_0009_backfills_run_of_old_steps() -> None:
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
    await asyncio.to_thread(command.upgrade, _cfg(), "0008")
    await _exec("INSERT INTO accounts (id) VALUES (1) ON CONFLICT DO NOTHING")
    # Исполнитель один, запуски идут друг за другом: плановые 1 и 3, ручной 2, пересечение 4/5
    # (такого не бывает — действие в нём неоднозначно) и незавершённый 6.
    await _run(1, 0, 10)
    await _run(2, 20, 30, by="admin")
    await _run(3, 40, 50)
    await _run(4, 45, 55)
    await _run(6, 57, None)
    steps = {
        101: (5, "scenario", {}),  # шаг планового 1
        102: (10, "scenario", {}),  # граница окна — ещё запуск 1
        103: (25, "manual", {}),  # шаг ручного запуска 2 (без ключа)
        104: (25, "manual", {"key": "manual:k1"}),  # ручная команда во время запуска 2
        105: (26, "manual", {"cls": "forbidden", "status": "rejected"}),  # запрет без ключа
        106: (5, "urgent", {}),  # реакция во время запуска 1
        107: (15, "scenario", {}),  # вне окон
        108: (25, "scenario", {}),  # шаг «сценария» в окне ручного запуска — не его
        109: (42, "scenario", {}),  # только в окне 3
        110: (47, "scenario", {}),  # в окнах 3 и 4 — неоднозначно
        111: (58, "scenario", {}),  # запуск 6 не завершён
    }
    for action_id, (minute, source, extra) in steps.items():
        await _action(action_id, minute, source, **extra)  # type: ignore[arg-type]
    await asyncio.to_thread(command.upgrade, _cfg(), "0009")
    rows = dict(await _exec("SELECT id, scenario_run_id FROM actions ORDER BY id"))
    assert rows == {
        101: 1,
        102: 1,
        103: 2,
        104: None,
        105: None,
        106: None,
        107: None,
        108: None,
        109: 3,
        110: None,
        111: None,
    }
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
