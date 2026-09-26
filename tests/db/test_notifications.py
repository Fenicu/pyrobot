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
