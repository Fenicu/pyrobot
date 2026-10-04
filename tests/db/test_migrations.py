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
        "tg_sessions",
        "tg_peers",
        "tg_chat_marks",
        "server_meta",
        "users",
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


async def test_0012_session_tables_hang_on_accounts_without_cascade() -> None:
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
    await asyncio.to_thread(command.upgrade, _cfg(), "0011")
    # Аккаунт 1 создаёт миграция 0001.
    await asyncio.to_thread(command.upgrade, _cfg(), "0012")
    assert {"tg_sessions", "tg_peers", "tg_chat_marks", "server_meta"} <= await _tables()
    await _exec("INSERT INTO tg_sessions (account_id, dc_id, date) VALUES (1, 2, 0)")
    await _exec("INSERT INTO tg_peers (account_id, id, type) VALUES (1, 10, 'user')")
    await _exec(
        "INSERT INTO tg_chat_marks (account_id, chat_id, from_id, msg_id) VALUES (1, -5, 0, 7)"
    )
    await _exec("INSERT INTO server_meta (key, value) VALUES ('key_check', '\\x01')")
    for sql in (
        "INSERT INTO tg_sessions (account_id, dc_id, date) VALUES (9, 2, 0)",
        "INSERT INTO tg_peers (account_id, id, type) VALUES (9, 10, 'user')",
        "INSERT INTO tg_peers (account_id, id, type) VALUES (1, 10, 'bot')",
        "INSERT INTO tg_chat_marks (account_id, chat_id, from_id, msg_id) VALUES (9, -5, 0, 7)",
        "INSERT INTO tg_chat_marks (account_id, chat_id, from_id, msg_id) VALUES (1, -5, 0, 8)",
        "INSERT INTO tg_chat_marks (account_id, chat_id, from_id, msg_id) VALUES (1, -5, 3, NULL)",
        # Аккаунт с данными сессии не удаляется: каскада нет.
        "DELETE FROM accounts WHERE id = 1",
    ):
        with pytest.raises(IntegrityError):
            await _exec(sql)
    await asyncio.to_thread(command.downgrade, _cfg(), "0011")
    assert not {"tg_sessions", "tg_peers", "tg_chat_marks", "server_meta"} & await _tables()
    await asyncio.to_thread(command.downgrade, _cfg(), "base")


async def _message(chat_id: int, msg_id: int, from_id: int | None, account_id: int = 1) -> None:
    await _exec(
        "INSERT INTO messages (account_id, chat_id, msg_id, revision, content_hash, kind, date,"
        " received_at, recovered, outgoing, from_id, text, events) VALUES (:a, :c, :m, 0, 'h',"
        " 'new', :at, :at, false, false, :f, 'x', '[]')",
        a=account_id,
        c=chat_id,
        m=msg_id,
        f=from_id,
        at=_at(msg_id),
    )


async def _marks() -> set[tuple[object, ...]]:
    return set(await _exec("SELECT account_id, chat_id, from_id, msg_id FROM tg_chat_marks"))


async def test_0013_seeds_marks_from_journal() -> None:
    game, smoothie, swinfo, swinfo_user = 227859379, -1001356300612, -1001109615116, 376592453
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
    await asyncio.to_thread(command.upgrade, _cfg(), "0012")
    # Чат приглашений к биржевикам — общий чат swinfo; пользователь swinfo — из настроек.
    data = '{"chats": {"swinfo_user_id": 555, "bulls_invite_chat_id": -1001109615116}}'
    await _exec(
        "INSERT INTO settings (account_id, version, data) VALUES (1, 3, CAST(:d AS jsonb))",
        d=data,
    )
    await _exec("INSERT INTO accounts (id, name) VALUES (2, 'Второй')")
    for chat_id, msg_id, from_id in (
        (game, 10, game),
        (game, 12, None),
        (swinfo, 50, 555),
        # Сообщение прежнего пользователя swinfo (значение по умолчанию) — другой отправитель.
        (swinfo, 70, swinfo_user),
        # Приглашение в общем чате: отметку чтения всего чата миграция не ставит.
        (swinfo, 80, 9),
    ):
        await _message(chat_id, msg_id, from_id)
    await _message(game, 99, game, account_id=2)
    await asyncio.to_thread(command.upgrade, _cfg(), "0013")
    # Канала смузи в журнале нет — нет и отметки: её поставит первый проход.
    assert await _marks() == {(1, game, 0, 12), (1, swinfo, 555, 50)}
    assert smoothie not in {chat for _, chat, _, _ in await _marks()}
    await asyncio.to_thread(command.downgrade, _cfg(), "0012")
    await asyncio.to_thread(command.downgrade, _cfg(), "base")


async def test_0013_without_settings_uses_chat_defaults() -> None:
    game, smoothie, swinfo, swinfo_user = 227859379, -1001356300612, -1001109615116, 376592453
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
    await asyncio.to_thread(command.upgrade, _cfg(), "0012")
    for chat_id, msg_id, from_id in (
        (game, 10, game),
        (smoothie, 7, None),
        (swinfo, 50, swinfo_user),
        (swinfo, 70, 9),
    ):
        await _message(chat_id, msg_id, from_id)
    await asyncio.to_thread(command.upgrade, _cfg(), "0013")
    assert await _marks() == {(1, game, 0, 10), (1, smoothie, 0, 7), (1, swinfo, swinfo_user, 50)}
    await asyncio.to_thread(command.downgrade, _cfg(), "base")


async def test_0014_users_become_owners() -> None:
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
    await asyncio.to_thread(command.upgrade, _cfg(), "0013")
    # до 0014: admin_users id=7 'admin' (аккаунты 1, 2, 3) и id=8 'old' (без аккаунтов),
    # auth_sessions(admin_user_id=7)
    await _exec(
        "INSERT INTO admin_users (id, login, password_hash, created_at, password_changed_at) "
        "VALUES (7, 'admin', 'h7', now(), now()), (8, 'old', 'h8', now(), now())"
    )
    await _exec("UPDATE accounts SET owner_id = 7 WHERE id = 1")
    await _exec("INSERT INTO accounts (id, name, owner_id) VALUES (2, 'acc2', 7), (3, 'acc3', 7)")
    await _exec(
        "INSERT INTO auth_sessions (id, token_hash, csrf_token, admin_user_id, expires_at) "
        "VALUES (101, 'th', 'csrf', 7, now() + interval '1 day')"
    )
    # upgrade 0014:
    await asyncio.to_thread(command.upgrade, _cfg(), "0014")
    tables = await _tables()
    assert "users" in tables and "admin_users" not in tables
    rows = await _exec(
        "SELECT id, login, role, max_accounts, disabled_at, disabled_reason, "
        "invited_by, last_login_at, deleting_at FROM users ORDER BY id"
    )
    u7 = rows[0]
    assert u7[0] == 7 and u7[1] == "admin" and u7[2] == "owner" and u7[3] == 10 and u7[4] is None
    u8 = rows[1]
    assert u8[0] == 8 and u8[1] == "old" and u8[2] == "owner" and u8[3] == 10 and u8[4] is None
    # сессия учётки 7 на месте, FK accounts.owner_id и auth_sessions → users
    s_rows = await _exec("SELECT admin_user_id FROM auth_sessions WHERE id = 101")
    assert s_rows == [(7,)]
    # downgrade 0013: таблица admin_users с теми же строками, лишних колонок нет
    await asyncio.to_thread(command.downgrade, _cfg(), "0013")
    tables_down = await _tables()
    assert "admin_users" in tables_down and "users" not in tables_down
    adm_rows = await _exec("SELECT id, login FROM admin_users ORDER BY id")
    assert adm_rows == [(7, "admin"), (8, "old")]
    await asyncio.to_thread(command.downgrade, _cfg(), "base")


async def test_0014_max_accounts_counts_accounts() -> None:
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
    await asyncio.to_thread(command.upgrade, _cfg(), "0013")
    await _exec(
        "INSERT INTO admin_users (id, login, password_hash, created_at, password_changed_at) "
        "VALUES (7, 'big', 'h7', now(), now())"
    )
    await _exec("UPDATE accounts SET owner_id = 7 WHERE id = 1")
    for i in range(2, 13):
        await _exec(f"INSERT INTO accounts (id, name, owner_id) VALUES ({i}, 'acc{i}', 7)")
    await asyncio.to_thread(command.upgrade, _cfg(), "0014")
    rows = await _exec("SELECT max_accounts FROM users WHERE id = 7")
    assert rows == [(12,)]
    await asyncio.to_thread(command.downgrade, _cfg(), "base")
