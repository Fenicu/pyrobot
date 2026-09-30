import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
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
        "ledger",
    } <= await _tables()
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
    assert await _tables() <= {"alembic_version"}


FK_INDEXES = {
    "ix_actions_scenario_run_id",
    "ix_unrecognized_message_id",
    "ix_scenario_runs_decision_id",
    "ix_metro_runs_scenario_run_id",
}
# Постраничное чтение по `id` внутри аккаунта (уведомления, история настроек) и привязка Telegram.
ACCOUNT_INDEXES = {
    "ix_notifications_account_id_id",
    "ix_settings_history_account_id_id",
    "uq_accounts_tg_user_id",
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
    assert FK_INDEXES | ACCOUNT_INDEXES <= indexes


def _at(minute: int) -> datetime:
    return datetime(2026, 9, 27, 12, tzinfo=UTC) + timedelta(minutes=minute)


async def _exec(sql: str, **params: object) -> list[tuple[object, ...]]:
    engine = create_async_engine(MIGTEST_DB_URL)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params)
            rows = [tuple(r) for r in result.all()] if result.returns_rows else []
    finally:
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


async def _at_0010(*, admin: bool) -> None:
    """База на 0010: аккаунт 1 с настройками, в которых привязка Telegram и режим движка."""
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
    await asyncio.to_thread(command.upgrade, _cfg(), "0010")
    if admin:
        await _exec("INSERT INTO admin_users (id, login, password_hash) VALUES (7, 'a', 'x')")
        await _exec("INSERT INTO admin_users (id, login, password_hash) VALUES (9, 'b', 'x')")
    data = '{"telegram": {"expected_user_id": 267519921}, "engine": {"mode": "live"}}'
    await _exec(
        "INSERT INTO settings (account_id, version, data) VALUES (1, 3, CAST(:d AS jsonb))",
        d=data,
    )


async def _account(account_id: int) -> dict[str, object]:
    engine = create_async_engine(MIGTEST_DB_URL)
    async with engine.connect() as conn:
        row = await conn.execute(text("SELECT * FROM accounts WHERE id = :id"), {"id": account_id})
        found = row.mappings().one()
    await engine.dispose()
    return dict(found)


async def _settings_data(account_id: int) -> dict[str, object]:
    [(data,)] = await _exec("SELECT data FROM settings WHERE account_id = :id", id=account_id)
    assert isinstance(data, dict)
    return data


async def test_0011_moves_binding_and_adopts_owner() -> None:
    await _at_0010(admin=True)
    await _exec("INSERT INTO accounts (id) VALUES (2)")
    await asyncio.to_thread(command.upgrade, _cfg(), "0011")
    account = await _account(1)
    assert account["owner_id"] == 7 and account["name"] == "Основной"
    assert account["status"] == "enabled" and account["tg_user_id"] == 267519921
    assert account["status_reason"] is None and account["engine_generation"] == 0
    assert account["lease_holder"] is None and account["lease_epoch"] == 0
    assert account["lease_expires_at"] is None and account["updated_at"] is not None
    settings_data = await _settings_data(1)
    assert "telegram" not in settings_data and settings_data["engine"] == {"mode": "live"}
    # Аккаунт без настроек: имя по id, привязки нет; владелец — первая учётка.
    second = await _account(2)
    assert second["name"] == "Аккаунт 2" and second["owner_id"] == 7
    assert second["tg_user_id"] is None
    # Счётчик id сдвинут: 0001 вставила аккаунт с явным id, `POST /accounts` упал бы на дубле.
    assert await _exec("SELECT nextval('accounts_id_seq')") == [(3,)]
    await asyncio.to_thread(command.downgrade, _cfg(), "0010")
    assert await _settings_data(1) == {
        "telegram": {"expected_user_id": 267519921},
        "engine": {"mode": "live"},
    }
    await asyncio.to_thread(command.downgrade, _cfg(), "base")


async def test_0011_without_admin_leaves_owner_null() -> None:
    await _at_0010(admin=False)
    await asyncio.to_thread(command.upgrade, _cfg(), "0011")
    account = await _account(1)
    assert account["owner_id"] is None and account["name"] == "Основной"
    assert account["tg_user_id"] == 267519921
    await asyncio.to_thread(command.downgrade, _cfg(), "base")


async def test_0011_constraints() -> None:
    await _at_0010(admin=True)
    await _exec("INSERT INTO accounts (id) VALUES (2)")
    await asyncio.to_thread(command.upgrade, _cfg(), "0011")
    for sql in (
        "UPDATE accounts SET status = 'bogus' WHERE id = 1",
        "INSERT INTO accounts (id, owner_id, name) VALUES (5, 7, 'Основной')",
        "UPDATE accounts SET tg_user_id = 267519921 WHERE id = 2",
        "DELETE FROM admin_users WHERE id = 7",
    ):
        with pytest.raises(IntegrityError):
            await _exec(sql)
    # Аккаунты без привязки и без владельца уникальность не связывает.
    await _exec("INSERT INTO accounts (id, name) VALUES (5, 'Один'), (6, 'Один')")
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
