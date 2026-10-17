from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from app.db.accounts import AccountRepo
from app.db.base import Database
from app.db.models import LedgerRow, MetricRow, User
from app.db.overview import OverviewReads
from app.engine.daily import LedgerDays, LedgerEntry, last_by_day
from app.engine.gametime import MSK

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)  # 12:00 МСК
TODAY = date(2026, 10, 17)
FLOOR = NOW - timedelta(days=365)


def msk(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, second, tzinfo=MSK).astimezone(UTC)


async def _user(db: Database, login: str) -> int:
    async with db.sessions() as session, session.begin():
        user = User(login=login, password_hash="x", role="user", max_accounts=10)
        session.add(user)
        await session.flush()
        return user.id


async def _metrics(
    db: Database, account_id: int, points: list[tuple[datetime, str, float]]
) -> None:
    async with db.sessions() as s, s.begin():
        s.add_all(MetricRow(account_id=account_id, ts=ts, key=k, value=v) for ts, k, v in points)


async def _ledger(
    db: Database,
    account_id: int,
    rows: list[tuple[datetime, str, dict[str, Any], dict[str, Any]]],
    *,
    recorded: datetime | None = None,
) -> None:
    async with db.sessions() as s, s.begin():
        s.add_all(
            LedgerRow(
                account_id=account_id,
                at=at,
                day=at.astimezone(MSK).date(),
                recorded_at=recorded or at,
                kind=kind,
                amounts=amounts,
                items=items,
                chat_id=1,
                msg_id=account_id * 100_000 + i + int(at.timestamp()),
                revision=0,
                content_hash="h",
                seq=0,
            )
            for i, (at, kind, amounts, items) in enumerate(rows)
        )


async def _totals(db: Database, owner: int, ids: list[int], days: int = 7, **kw: Any) -> Any:
    first = TODAY - timedelta(days=days - 1)
    args = {"now": NOW, "metrics_floor": FLOOR} | kw
    return await OverviewReads(db).totals(owner, ids, first, args["now"], args["metrics_floor"])


async def test_last_value_per_msk_day_and_value_before_window(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    a = (await AccountRepo(clean_db).create(uid, "a")).id
    points = [
        (msk(9, 10), "money", 7.0),  # давно до окна, но это опорная точка
        (msk(10, 23, 59, 59), "money", 1.0),  # ровно перед окном 7 дней (11..17)
        (msk(11, 0, 0, 0), "money", 2.0),
        (msk(11, 12), "money", 3.0),
        (msk(13, 23, 59, 59), "money", 4.0),
        (msk(14, 0, 0, 0), "money", 5.0),
        (msk(16, 8), "exp", 9.0),
        (msk(17, 11), "level", 12.0),
    ]
    await _metrics(clean_db, a, points)
    [got] = await _totals(clean_db, uid, [a])
    assert got.account_id == a
    assert got.last == last_by_day([p for p in points if p[0] >= msk(10, 0)])
    assert got.before == {"money": 1.0}


async def test_value_before_window_has_no_age_limit_but_respects_floor(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    a = (await AccountRepo(clean_db).create(uid, "a")).id
    await _metrics(
        clean_db,
        a,
        [
            (NOW - timedelta(days=200), "money", 1.0),
            (NOW - timedelta(days=100), "exp", 2.0),
            (NOW - timedelta(days=500), "raw", 3.0),
        ],
    )
    [got] = await _totals(clean_db, uid, [a], metrics_floor=NOW - timedelta(days=365))
    assert got.before == {"money": 1.0, "exp": 2.0}
    [short] = await _totals(clean_db, uid, [a], metrics_floor=NOW - timedelta(days=150))
    assert short.before == {"exp": 2.0}


async def test_points_at_or_after_snapshot_instant_excluded(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    a = (await AccountRepo(clean_db).create(uid, "a")).id
    await _metrics(
        clean_db,
        a,
        [
            (NOW - timedelta(seconds=1), "money", 1.0),
            (NOW, "money", 2.0),
            (NOW + timedelta(minutes=5), "money", 3.0),
        ],
    )
    [got] = await _totals(clean_db, uid, [a])
    assert got.last == {"money": {TODAY: 1.0}}


async def test_same_instant_ties_take_latest_id(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    a = (await AccountRepo(clean_db).create(uid, "a")).id
    at = msk(15, 10)
    await _metrics(clean_db, a, [(at, "money", 1.0), (at, "money", 2.0)])
    [got] = await _totals(clean_db, uid, [a])
    assert got.last == {"money": {date(2026, 10, 15): 2.0}}


async def test_ledger_fold_equals_record_by_record_fold(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    a = (await AccountRepo(clean_db).create(uid, "a")).id
    rows = [
        (msk(16, 10), "battle", {"money": 100, "exp": 50}, {}),
        (msk(16, 11), "battle", {"money": 40}, {}),
        (msk(16, 12), "deed", {"exp": 9}, {"Нитки": 2}),
        (msk(16, 13), "deed", {}, {"Нитки": 1, "Флюс": 1}),
        (msk(16, 14), "hotel", {"money": -30}, {}),
        (msk(16, 15), "gadget_buy", {"money": -500}, {"gadget": 1}),
        (msk(17, 1), "task", {"trophies": 90}, {}),
        (msk(17, 2), "book", {}, {}),
        (msk(17, 3), "book", {"knowledge": 3, "exp": 263}, {"Флюс": 1}),
        (msk(10, 23, 59, 59), "battle", {"money": 1}, {}),
        (msk(11, 0, 0, 0), "battle", {"money": 2}, {}),
    ]
    await _ledger(clean_db, a, rows)
    [got] = await _totals(clean_db, uid, [a])
    first = date(2026, 10, 11)
    want = LedgerDays.of(
        LedgerEntry(at.astimezone(MSK).date(), kind, amounts, items)
        for at, kind, amounts, items in rows
        if at.astimezone(MSK).date() >= first
    )
    assert got.ledger == want


async def test_ledger_ignores_non_number_values_and_future_records(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    a = (await AccountRepo(clean_db).create(uid, "a")).id
    await _ledger(
        clean_db,
        a,
        [
            (msk(16, 10), "battle", {"money": 10, "note": "x", "nested": {"a": 1}}, {"Флюс": 1}),
            (NOW + timedelta(minutes=1), "battle", {"money": 99}, {}),
            (NOW, "battle", {"money": 98}, {}),
        ],
    )
    [got] = await _totals(clean_db, uid, [a])
    assert got.ledger == LedgerDays.of(
        [LedgerEntry(date(2026, 10, 16), "battle", {"money": 10}, {"Флюс": 1})]
    )


async def test_ledger_since_is_first_recording_even_before_window(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    a, b = [(await AccountRepo(clean_db).create(uid, n)).id for n in "ab"]
    await _ledger(clean_db, a, [(msk(1, 10), "book", {}, {})], recorded=msk(3, 22))
    await _ledger(clean_db, a, [(msk(16, 10), "book", {"exp": 1}, {})], recorded=msk(16, 10))
    got = {t.account_id: t for t in await _totals(clean_db, uid, [a, b])}
    assert got[a].ledger_since == date(2026, 10, 3)
    assert got[b].ledger_since is None
    assert got[b].ledger == LedgerDays() and got[b].last == {} and got[b].before == {}


async def test_several_accounts_not_mixed_and_ordered_by_id(clean_db: Database) -> None:
    uid = await _user(clean_db, "alice")
    repo = AccountRepo(clean_db)
    a, b = (await repo.create(uid, "a")).id, (await repo.create(uid, "b")).id
    await _metrics(clean_db, a, [(msk(16, 10), "money", 1.0)])
    await _metrics(clean_db, b, [(msk(16, 10), "money", 2.0)])
    await _ledger(clean_db, b, [(msk(16, 10), "battle", {"money": 5}, {})])
    got = await _totals(clean_db, uid, [b, a])
    assert [t.account_id for t in got] == sorted([a, b])
    by_id = {t.account_id: t for t in got}
    assert (
        by_id[a].last == {"money": {date(2026, 10, 16): 1.0}} and by_id[a].ledger == LedgerDays()
    )
    assert by_id[b].last == {"money": {date(2026, 10, 16): 2.0}}
    assert by_id[b].ledger.days[date(2026, 10, 16)].income["battle"].count == 1  # type: ignore[attr-defined]


async def test_foreign_unknown_and_deleting_accounts_excluded(clean_db: Database) -> None:
    repo = AccountRepo(clean_db)
    alice, bob = await _user(clean_db, "alice"), await _user(clean_db, "bob")
    mine = (await repo.create(alice, "mine")).id
    gone = (await repo.create(alice, "gone")).id
    theirs = (await repo.create(bob, "theirs")).id
    await repo.mark_deleting(gone)
    for acc in (mine, gone, theirs):
        await _metrics(clean_db, acc, [(msk(16, 10), "money", 1.0)])
    got = await _totals(clean_db, alice, [mine, gone, theirs, 9999])
    assert [t.account_id for t in got] == [mine]
    assert await _totals(clean_db, alice, []) == []
