from datetime import UTC, date, datetime, timedelta

import pytest

from app.db.base import Database
from app.db.models import LedgerRow, MetricRow
from app.db.reads import DbReads
from app.engine.daily import LedgerEntry, last_by_day
from app.engine.gametime import MSK

pytestmark = pytest.mark.db


def msk(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, second, tzinfo=MSK).astimezone(UTC)


async def test_last_value_per_msk_day_matches_pure_version(clean_db: Database) -> None:
    points = [
        (msk(26, 23, 59, 59), "money", 1.0),
        (msk(27, 0, 0, 0), "money", 2.0),
        (msk(27, 12), "money", 3.0),
        (msk(27, 23, 59, 59), "money", 4.0),
        (msk(28, 0, 1), "money", 5.0),
        (msk(28, 2), "exp", 7.0),
        (msk(25, 10), "money", 0.5),
        (msk(28, 20), "money", 99.0),
    ]
    async with clean_db.sessions() as s, s.begin():
        s.add_all(MetricRow(account_id=1, ts=ts, key=k, value=v) for ts, k, v in points)
    reads = DbReads(clean_db, 1)
    until = msk(28, 14)
    got = await reads.day_values(["money", "exp"], date(2026, 9, 26), until)
    shown = [
        p for p in points if p[0] < until and p[0].astimezone(MSK).date() >= date(2026, 9, 26)
    ]
    assert got == last_by_day(shown)
    assert got["money"] == {date(2026, 9, 26): 1.0, date(2026, 9, 27): 4.0, date(2026, 9, 28): 5.0}


async def test_ledger_entries_from_day_and_first_day(clean_db: Database) -> None:
    reads = DbReads(clean_db, 1)
    assert await reads.ledger_entries(date(2026, 9, 27)) == ([], None)
    rows = [
        (date(2026, 9, 20), "book", {"exp": 1}, {}),
        (date(2026, 9, 27), "deed", {"exp": 2}, {"Нитки": 1}),
        (date(2026, 9, 28), "task", {"trophies": 90}, {}),
    ]
    async with clean_db.sessions() as s, s.begin():
        s.add_all(
            LedgerRow(
                account_id=1,
                at=datetime(d.year, d.month, d.day, 12, tzinfo=UTC) - timedelta(hours=1),
                day=d,
                kind=kind,
                amounts=amounts,
                items=items,
                chat_id=1,
                msg_id=i,
                revision=0,
                content_hash="h",
                seq=0,
            )
            for i, (d, kind, amounts, items) in enumerate(rows)
        )
    entries, since = await reads.ledger_entries(date(2026, 9, 27))
    assert since == date(2026, 9, 20)
    assert entries == [
        LedgerEntry(date(2026, 9, 27), "deed", {"exp": 2}, {"Нитки": 1}),
        LedgerEntry(date(2026, 9, 28), "task", {"trophies": 90}, {}),
    ]
