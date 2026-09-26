import pytest

from app.db.base import Database
from app.db.journal import DbJournal
from app.engine.events import AntiFlood
from tests.engine.helpers import make_msg

pytestmark = pytest.mark.db


async def test_append_dedup_and_snapshot(clean_db: Database) -> None:
    journal = DbJournal(clean_db, account_id=1)
    assert await journal.load_state() == ({}, 0)
    m = make_msg("Полегче, йоу")
    first = await journal.append(m, [AntiFlood()], {"x": 1}, 1)
    assert first is not None
    assert await journal.append(m, [AntiFlood()], None, 1) is None
    assert await journal.load_state() == ({"x": 1}, 1)
    edit = make_msg("Полегче, йоу", kind="edit", revision=123)
    second = await journal.append(edit, [], None, 1)
    assert second is not None and second > first
