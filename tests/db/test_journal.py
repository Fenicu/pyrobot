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


async def test_metrics_and_unrecognized_rows(clean_db: Database) -> None:
    from sqlalchemy import select

    from app.db.models import MetricRow, UnrecognizedRow
    from app.engine.events import Unrecognized

    journal = DbJournal(clean_db, account_id=1)
    msg = make_msg("совсем непонятное", msg_id=5)
    journal_id = await journal.append(
        msg, [Unrecognized(first_line="совсем непонятное")], {"x": 1}, 1, metrics={"money": 10.0}
    )
    assert journal_id is not None
    again = await journal.append(msg, [Unrecognized(first_line="x")], None, 1, metrics={"a": 1.0})
    assert again is None
    async with clean_db.sessions() as session:
        metrics = (await session.scalars(select(MetricRow))).all()
        unknown = (await session.scalars(select(UnrecognizedRow))).all()
    assert [(m.key, m.value, m.ts) for m in metrics] == [("money", 10.0, msg.date)]
    assert [(u.message_id, u.msg_id, u.first_line, u.acked) for u in unknown] == [
        (journal_id, 5, "совсем непонятное", False)
    ]
