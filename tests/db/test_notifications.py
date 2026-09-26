import pytest

from app.db.base import Database
from app.db.notifications import DbNotifier

pytestmark = pytest.mark.db


async def test_notify_persists(clean_db: Database) -> None:
    n = DbNotifier(clean_db, account_id=1)
    await n.notify("warn", "test_code", "проверка")
    rows = await n.recent()
    assert [(r.level, r.code, r.text) for r in rows] == [("warn", "test_code", "проверка")]
