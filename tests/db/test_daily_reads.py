from datetime import UTC, date, datetime, timedelta

import pytest

from app.db.base import Database
from app.db.models import LedgerRow, MetricRow
from app.db.reads import DbReads, UpgradeProgress
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
                recorded_at=datetime(d.year, d.month, d.day, 12, tzinfo=UTC),
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


async def test_ledger_since_is_first_recording_not_effect_day(clean_db: Database) -> None:
    # Журнал запущен 12.09: /fb в тот день отдал отчёт о битве 09.09 — эффект датирован 09.09, но
    # дни до 12.09 журнал не видел и полными не становятся.
    from app.db.journal import DbJournal
    from app.engine.state.ledger import Effect
    from tests.engine.helpers import make_msg

    journal = DbJournal(clean_db, 1)
    received = msk(12, 3, 12)
    battle = msk(9, 18, 30)
    report = make_msg("fb", msg_id=1, date=received, received_at=received)
    await journal.append(report, [], None, 1, effects=[Effect("factory", {"exp": 1}, at=battle)])
    book = make_msg("book", msg_id=2, date=msk(12, 9), received_at=msk(12, 9))
    await journal.append(book, [], None, 1, effects=[Effect("book", {"exp": 5})])
    entries, since = await DbReads(clean_db, 1).ledger_entries(date(2026, 9, 1))
    assert [e.day for e in entries] == [date(2026, 9, 9), date(2026, 9, 12)]
    assert since == date(2026, 9, 12)


async def test_upgrade_progress_by_slot_since_start(clean_db: Database) -> None:
    start = msk(28, 12)
    effects = [
        (start - timedelta(minutes=5), "right", "red", "ok"),
        (start, "right", "red", "ok"),
        (start + timedelta(minutes=1), "right", "red", "fail"),
        (start + timedelta(minutes=2), "right", "white", "ok"),
        (start + timedelta(minutes=3), "left", "blue", "ok"),
    ]
    async with clean_db.sessions() as s, s.begin():
        s.add_all(
            LedgerRow(
                account_id=1,
                at=at,
                recorded_at=at,
                day=at.astimezone(MSK).date(),
                kind="gadget_upgrade",
                amounts={f"upgrades_{used}": -1},
                items={f"up:{slot}": 1, result: 1},
                chat_id=1,
                msg_id=i,
                revision=0,
                content_hash="h",
                seq=0,
            )
            for i, (at, slot, used, result) in enumerate(effects)
        )
        s.add(
            LedgerRow(
                account_id=1,
                at=start,
                recorded_at=start,
                day=start.astimezone(MSK).date(),
                kind="gadget_buy",
                amounts={"money": -9},
                items={"up:right": 1},
                chat_id=1,
                msg_id=99,
                revision=0,
                content_hash="h",
                seq=0,
            )
        )
    reads = DbReads(clean_db, 1)
    assert await reads.upgrade_progress("right", start) == UpgradeProgress(
        attempts=3, ok=2, fail=1, spent={"white": 1, "blue": 0, "red": 2}
    )
    assert await DbReads(clean_db, 2).upgrade_progress("right", start) == UpgradeProgress(
        attempts=0, ok=0, fail=0, spent={"white": 0, "blue": 0, "red": 0}
    )


def upgrade_row(at: datetime, msg_id: int) -> LedgerRow:
    return LedgerRow(
        account_id=1,
        at=at,
        recorded_at=at,
        day=at.astimezone(MSK).date(),
        kind="gadget_upgrade",
        amounts={"upgrades_white": -1},
        items={"up:right": 1, "ok": 1},
        chat_id=1,
        msg_id=msg_id,
        revision=0,
        content_hash="h",
        seq=0,
    )


async def test_upgrade_progress_from_start_second_until_end(clean_db: Database) -> None:
    # Время записей — дата сообщения игры, целые секунды; старт задачи — с долями секунды.
    start = msk(28, 12) + timedelta(microseconds=700_000)
    end = start + timedelta(minutes=2)
    moments = [
        msk(28, 12),
        start + timedelta(minutes=1),
        msk(28, 12, 2),
        end + timedelta(seconds=1),
    ]
    async with clean_db.sessions() as s, s.begin():
        s.add_all(upgrade_row(at, i) for i, at in enumerate(moments))
    reads = DbReads(clean_db, 1)
    assert (await reads.upgrade_progress("right", start)).attempts == 4
    assert (await reads.upgrade_progress("right", start, end)).attempts == 3
