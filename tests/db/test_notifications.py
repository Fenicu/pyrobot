import pytest

from app.db.base import Database
from app.db.notifications import DbNotifier

pytestmark = pytest.mark.db


async def test_notify_persists(clean_db: Database) -> None:
    n = DbNotifier(clean_db, account_id=1)
    await n.notify("warn", "test_code", "проверка")
    rows = await n.recent()
    assert [(r.level, r.code, r.text) for r in rows] == [("warn", "test_code", "проверка")]


async def test_notify_with_unreachable_db_does_not_raise() -> None:
    """DbNotifier.notify must not raise even if database is unreachable."""
    unreachable_db = Database("postgresql+asyncpg://pyrobot:pyrobot@127.0.0.1:1/nope")
    try:
        n = DbNotifier(unreachable_db, account_id=1)
        await n.notify("error", "test_code", "should not raise")
    finally:
        await unreachable_db.engine.dispose()


async def test_listeners_get_saved_row(clean_db: Database) -> None:
    n = DbNotifier(clean_db, account_id=1)
    seen: list[tuple[int, str]] = []

    def broken(row: object) -> None:
        raise RuntimeError("listener down")

    n.listeners += [lambda row: seen.append((row.id, row.code)), broken]
    await n.notify("info", "a", "x")
    await n.notify("info", "b", "y")
    assert [code for _, code in seen] == ["a", "b"] and seen[0][0] < seen[1][0]
    assert len(await n.recent()) == 2


async def test_server_notifications_invisible_to_accounts(clean_db: Database) -> None:
    from app.db.notifications import ServerNotifier

    server = ServerNotifier(clean_db)
    account = DbNotifier(clean_db, account_id=1)
    await server.notify("error", "account_error", "test server error")
    assert await account.recent() == []
    rows = await server.recent()
    assert len(rows) == 1
    assert rows[0].account_id is None
    assert rows[0].code == "account_error"
    assert rows[0].text == "test server error"


async def test_server_notifier_mark_read(clean_db: Database) -> None:
    from app.db.notifications import ServerNotifier

    server = ServerNotifier(clean_db)
    await server.notify("error", "e1", "test 1")
    await server.notify("warn", "w2", "test 2")
    rows = await server.recent()
    assert len(rows) == 2
    assert all(not r.read for r in rows)
    # rows are ordered desc, so rows[1] is e1 (older)
    e1_id = rows[1].id
    updated = await server.mark_read(e1_id)
    assert updated == 1
    rows_after = await server.recent()
    read_map = {r.code: r.read for r in rows_after}
    assert read_map["e1"] is True
    assert read_map["w2"] is False
