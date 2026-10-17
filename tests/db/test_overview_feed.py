from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.db.accounts import AccountRepo
from app.db.base import Database
from app.db.models import LedgerRow, NotificationRow, User
from app.db.overview import FEED_TYPES, MANUAL_CODES, FeedCursor, FeedEvent, OverviewReads

pytestmark = pytest.mark.db

T0 = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
FLOOR = date(2026, 9, 17)


def at(minutes: int = 0) -> datetime:
    return T0 - timedelta(minutes=minutes)


async def _user(db: Database, login: str) -> int:
    async with db.sessions() as session, session.begin():
        user = User(login=login, password_hash="x", role="user", max_accounts=10)
        session.add(user)
        await session.flush()
        return user.id


async def _account(db: Database, owner: int, name: str = "a") -> int:
    return (await AccountRepo(db).create(owner, name)).id


_seq = 0


async def ledger(
    db: Database,
    account: int,
    when: datetime,
    kind: str = "battle",
    amounts: dict[str, Any] | None = None,
    items: dict[str, Any] | None = None,
    *,
    day: date | None = None,
) -> int:
    global _seq
    _seq += 1
    async with db.sessions() as s, s.begin():
        row = LedgerRow(
            account_id=account,
            at=when,
            day=day or when.date(),
            recorded_at=when,
            kind=kind,
            amounts=amounts or {"money": 1},
            items=items or {},
            chat_id=1,
            msg_id=_seq,
            revision=0,
            content_hash="h",
            seq=0,
        )
        s.add(row)
        await s.flush()
        return row.id


async def note(
    db: Database,
    account: int | None,
    when: datetime,
    code: str = "scenario_failed",
    level: str = "error",
    text: str = "boom",
    read: bool = False,
) -> int:
    async with db.sessions() as s, s.begin():
        row = NotificationRow(
            account_id=account, created_at=when, code=code, level=level, text=text, read=read
        )
        s.add(row)
        await s.flush()
        return row.id


def keys(events: list[FeedEvent]) -> list[str]:
    return [e.key for e in events]


async def first_page(
    db: Database,
    owner: int,
    ids: list[int] | None = None,
    types: Any = FEED_TYPES,
    limit: int = 50,
):  # type: ignore[no-untyped-def]
    return await OverviewReads(db).feed_page(
        owner, ids, list(types), limit=limit, cursor=None, ledger_floor=FLOOR
    )


async def test_two_sources_merged_newest_first_notification_wins_a_tie(clean_db: Database) -> None:
    owner = await _user(clean_db, "alice")
    a = await _account(clean_db, owner)
    l1 = await ledger(clean_db, a, at(30))
    n1 = await note(clean_db, a, at(20))
    l2 = await ledger(clean_db, a, at(10))
    n2 = await note(clean_db, a, at(10))
    page = await first_page(clean_db, owner)
    assert keys(page.events) == [f"n:{n2}", f"l:{l2}", f"n:{n1}", f"l:{l1}"]
    assert page.has_more is False
    assert page.watermark == (max(l1, l2), max(n1, n2))


async def test_event_fields_for_both_sources(clean_db: Database) -> None:
    owner = await _user(clean_db, "alice")
    a = await _account(clean_db, owner)
    await ledger(clean_db, a, at(2), "battle", {"money": 1400, "exp": 800}, {"Нитки": 1})
    await ledger(clean_db, a, at(3), "lottery_skills")
    await note(clean_db, a, at(1), "scenario_failed", "error", "scenario deed:learn failed")
    await note(clean_db, a, at(4), "engine_paused", "info", "planner paused by admin")
    got = (await first_page(clean_db, owner)).events
    n_alert, battle, lottery, manual = got
    assert (n_alert.type, n_alert.kind, n_alert.level, n_alert.text) == (
        "alert",
        "scenario_failed",
        "error",
        "scenario deed:learn failed",
    )
    assert (n_alert.amounts, n_alert.items) == (None, None)
    assert (battle.type, battle.kind, battle.level, battle.text) == (
        "battle",
        "battle",
        None,
        None,
    )
    assert (battle.amounts, battle.items) == ({"money": 1400, "exp": 800}, {"Нитки": 1})
    assert (lottery.type, lottery.kind) == ("lottery", "lottery_skills")
    assert (manual.type, manual.kind, manual.level) == ("manual", "engine_paused", "info")
    assert battle.at == at(2) and battle.account_id == a


async def test_identical_timestamps_paginate_without_duplicates_or_gaps(
    clean_db: Database,
) -> None:
    owner = await _user(clean_db, "alice")
    a = await _account(clean_db, owner)
    b = await _account(clean_db, owner, "b")
    made: list[str] = []
    for i in range(14):
        made.append(f"l:{await ledger(clean_db, a if i % 2 else b, at(5))}")
        made.append(f"n:{await note(clean_db, a if i % 3 else b, at(5))}")
    # Плюс записи с другим временем по краям.
    made.append(f"l:{await ledger(clean_db, a, at(1))}")
    made.append(f"n:{await note(clean_db, b, at(9))}")
    reads = OverviewReads(clean_db)
    seen: list[str] = []
    cursor: FeedCursor | None = None
    pages = 0
    while True:
        page = await reads.feed_page(
            owner, None, list(FEED_TYPES), limit=6, cursor=cursor, ledger_floor=FLOOR
        )
        pages += 1
        assert pages < 20, "pagination does not terminate"
        seen += keys(page.events)
        if not page.has_more:
            break
        last = page.events[-1]
        cursor = FeedCursor(last.at, last.rank, last.id)
        # Курсор держит ранг: страница обрывается и посреди равных записей двух источников.
    assert pages >= 5
    assert len(seen) == len(set(seen)) == len(made)
    assert set(seen) == set(made)
    # Порядок (время, ранг, id) по убыванию целиком: новейшая, затем равные (уведомления раньше
    # журнала, внутри источника по id), затем самая старая.
    ties = sorted(
        (k for k in made if k not in (made[-2], made[-1])),
        key=lambda k: (k[0] == "n", int(k[2:])),
        reverse=True,
    )
    expected = [made[-2], *ties, made[-1]]
    assert seen == expected


async def test_cursor_boundary_inside_a_tie_of_two_sources(clean_db: Database) -> None:
    owner = await _user(clean_db, "alice")
    a = await _account(clean_db, owner)
    l1 = await ledger(clean_db, a, at(5))
    n1 = await note(clean_db, a, at(5))
    n2 = await note(clean_db, a, at(5))
    reads = OverviewReads(clean_db)
    one = await reads.feed_page(
        owner, None, list(FEED_TYPES), limit=1, cursor=None, ledger_floor=FLOOR
    )
    assert keys(one.events) == [f"n:{n2}"] and one.has_more
    two = await reads.feed_page(
        owner, None, list(FEED_TYPES), limit=5, cursor=FeedCursor(at(5), 1, n2), ledger_floor=FLOOR
    )
    assert keys(two.events) == [f"n:{n1}", f"l:{l1}"]
    # Курсор из ряда ledger (0) не отдаёт назад уведомления того же момента (ранг выше).
    three = await reads.feed_page(
        owner, None, list(FEED_TYPES), limit=5, cursor=FeedCursor(at(5), 0, l1), ledger_floor=FLOOR
    )
    assert three.events == []


async def test_filter_by_account_and_foreign_ids_dropped(clean_db: Database) -> None:
    alice, bob = await _user(clean_db, "alice"), await _user(clean_db, "bob")
    a1, a2 = await _account(clean_db, alice, "a1"), await _account(clean_db, alice, "a2")
    foreign = await _account(clean_db, bob, "b")
    for acc in (a1, a2, foreign):
        await ledger(clean_db, acc, at(5))
        await note(clean_db, acc, at(6))
    only_a2 = await first_page(clean_db, alice, [a2])
    assert {e.account_id for e in only_a2.events} == {a2} and len(only_a2.events) == 2
    mixed = await first_page(clean_db, alice, [a2, foreign, 9999])
    assert keys(mixed.events) == keys(only_a2.events) and mixed.watermark == only_a2.watermark
    everyone = await first_page(clean_db, alice, None)
    assert {e.account_id for e in everyone.events} == {a1, a2}
    nothing = await first_page(clean_db, alice, [foreign, 9999])
    assert nothing.events == [] and nothing.watermark == (0, 0) and not nothing.has_more
    assert (await first_page(clean_db, alice, [])).events == []


async def test_deleting_and_server_level_rows_excluded(clean_db: Database) -> None:
    owner = await _user(clean_db, "alice")
    keep = await _account(clean_db, owner, "keep")
    gone = await _account(clean_db, owner, "gone")
    await AccountRepo(clean_db).mark_deleting(gone)
    await ledger(clean_db, keep, at(5))
    await ledger(clean_db, gone, at(5))
    await note(clean_db, gone, at(5))
    await note(clean_db, None, at(4), "server_thing", "error")
    page = await first_page(clean_db, owner)
    assert {e.account_id for e in page.events} == {keep} and len(page.events) == 1


@pytest.mark.parametrize(
    ("types", "kinds", "codes"),
    [
        (["levelup"], {"levelup"}, set()),
        (["battle"], {"battle"}, set()),
        (["factory"], {"factory"}, set()),
        (["metro"], {"metro"}, set()),
        (["lottery"], {"lottery_win", "lottery_skills"}, set()),
        (["alert"], set(), {"scenario_failed", "tg_slow"}),
        (["manual"], set(), set(MANUAL_CODES)),
        (["battle", "manual"], {"battle"}, set(MANUAL_CODES)),
        (
            list(FEED_TYPES),
            {"levelup", "battle", "factory", "metro", "lottery_win", "lottery_skills"},
            {"scenario_failed", "tg_slow", *MANUAL_CODES},
        ),
    ],
)
async def test_type_filters(
    clean_db: Database, types: list[str], kinds: set[str], codes: set[str]
) -> None:
    owner = await _user(clean_db, "alice")
    a = await _account(clean_db, owner)
    for i, kind in enumerate(
        [
            "levelup",
            "battle",
            "factory",
            "metro",
            "lottery_win",
            "lottery_skills",
            "book",
            "deed",
            "hotel",
        ]
    ):
        await ledger(clean_db, a, at(100 + i), kind)
    await note(clean_db, a, at(50), "scenario_failed", "error")
    await note(clean_db, a, at(51), "tg_slow", "warn")
    await note(clean_db, a, at(52), "gadget_bought", "info")
    for i, code in enumerate(MANUAL_CODES):
        await note(clean_db, a, at(60 + i), code, "info")
    page = await first_page(clean_db, owner, None, types)
    got_kinds = {e.kind for e in page.events if e.key.startswith("l:")}
    got_codes = {e.kind for e in page.events if e.key.startswith("n:")}
    assert (got_kinds, got_codes) == (kinds, codes)


async def test_manual_codes_are_exactly_the_four_audit_codes() -> None:
    assert set(MANUAL_CODES) == {
        "engine_paused",
        "engine_resumed",
        "manual_control_on",
        "manual_control_off",
    }


async def test_feed_does_not_touch_read_flag(clean_db: Database) -> None:
    owner = await _user(clean_db, "alice")
    a = await _account(clean_db, owner)
    unread = await note(clean_db, a, at(1))
    seen = await note(clean_db, a, at(2), read=True)
    reads = OverviewReads(clean_db)
    await first_page(clean_db, owner)
    await reads.feed_after(
        owner, None, list(FEED_TYPES), after=(0, 0), limit=10, ledger_floor=FLOOR
    )
    async with clean_db.sessions() as s:
        flags = dict((await s.execute(select(NotificationRow.id, NotificationRow.read))).all())
    assert flags == {unread: False, seen: True}


async def test_ledger_floor_applies_to_ledger_only(clean_db: Database) -> None:
    owner = await _user(clean_db, "alice")
    a = await _account(clean_db, owner)
    old_day = date(2026, 9, 10)
    await ledger(clean_db, a, at(5), day=old_day)
    fresh = await ledger(clean_db, a, at(6), day=FLOOR)
    old_note = await note(clean_db, a, T0 - timedelta(days=60))
    page = await first_page(clean_db, owner)
    assert set(keys(page.events)) == {f"l:{fresh}", f"n:{old_note}"}


async def test_late_record_with_old_time_comes_through_after_not_first_page(
    clean_db: Database,
) -> None:
    owner = await _user(clean_db, "alice")
    a = await _account(clean_db, owner)
    await ledger(clean_db, a, at(1))
    await note(clean_db, a, at(2))
    reads = OverviewReads(clean_db)
    first = await reads.feed_page(
        owner, None, list(FEED_TYPES), limit=50, cursor=None, ledger_floor=FLOOR
    )
    assert first.watermark is not None
    # Отчёт о битве, записанный позже, датирован давно: id больше, время старее всех.
    late = await ledger(clean_db, a, at(500))
    late_note = await note(clean_db, a, at(400), "tg_slow", "warn")
    assert f"l:{late}" not in keys(first.events)
    got = await reads.feed_after(
        owner, None, list(FEED_TYPES), after=first.watermark, limit=50, ledger_floor=FLOOR
    )
    assert set(keys(got.events)) == {f"l:{late}", f"n:{late_note}"}
    assert got.watermark == (late, late_note) and got.has_more is False
    again = await reads.feed_after(
        owner, None, list(FEED_TYPES), after=got.watermark, limit=50, ledger_floor=FLOOR
    )
    assert again.events == [] and again.watermark == got.watermark


async def test_influx_larger_than_page_is_picked_up_without_gaps(clean_db: Database) -> None:
    owner = await _user(clean_db, "alice")
    a = await _account(clean_db, owner)
    reads = OverviewReads(clean_db)
    first = await reads.feed_page(
        owner, None, list(FEED_TYPES), limit=5, cursor=None, ledger_floor=FLOOR
    )
    assert first.watermark == (0, 0)
    made: set[str] = set()
    for i in range(13):
        made.add(f"l:{await ledger(clean_db, a, at(1000 - i))}")
    for i in range(9):
        made.add(f"n:{await note(clean_db, a, at(2000 - i))}")
    mark = first.watermark
    got: list[str] = []
    calls = 0
    while True:
        part = await reads.feed_after(
            owner, None, list(FEED_TYPES), after=mark, limit=4, ledger_floor=FLOOR
        )
        calls += 1
        assert len(part.events) <= 4
        got += keys(part.events)
        mark = part.watermark
        if not part.has_more:
            break
    assert calls == 6
    assert len(got) == len(set(got)) == len(made) and set(got) == made
    # Внутри источника - по возрастанию id.
    for prefix in ("l:", "n:"):
        ids = [int(k[2:]) for k in got if k.startswith(prefix)]
        assert ids == sorted(ids)


async def test_after_respects_filters_and_accounts(clean_db: Database) -> None:
    alice, bob = await _user(clean_db, "alice"), await _user(clean_db, "bob")
    a1, a2 = await _account(clean_db, alice, "a1"), await _account(clean_db, alice, "a2")
    foreign = await _account(clean_db, bob, "b")
    reads = OverviewReads(clean_db)
    await ledger(clean_db, a1, at(5), "battle")
    wanted = await ledger(clean_db, a2, at(5), "battle")
    await ledger(clean_db, a2, at(5), "book")
    await ledger(clean_db, foreign, at(5), "battle")
    await note(clean_db, a2, at(5), "gadget_bought", "info")
    got = await reads.feed_after(
        alice, [a2], ["battle"], after=(0, 0), limit=50, ledger_floor=FLOOR
    )
    assert keys(got.events) == [f"l:{wanted}"] and got.watermark == (wanted, 0)


async def test_watermark_follows_selected_accounts_and_types(clean_db: Database) -> None:
    owner = await _user(clean_db, "alice")
    a1, a2 = await _account(clean_db, owner, "a1"), await _account(clean_db, owner, "a2")
    l_a1 = await ledger(clean_db, a1, at(5), "battle")
    l_a2 = await ledger(clean_db, a2, at(5), "battle")
    n_a2 = await note(clean_db, a2, at(5), "tg_slow", "warn")
    await ledger(clean_db, a2, at(5), "book")
    assert (await first_page(clean_db, owner, [a1])).watermark == (l_a1, 0)
    assert (await first_page(clean_db, owner, [a2], ["battle"])).watermark == (l_a2, 0)
    assert (await first_page(clean_db, owner, [a2], ["alert"])).watermark == (0, n_a2)


async def test_cursor_page_has_no_watermark(clean_db: Database) -> None:
    owner = await _user(clean_db, "alice")
    a = await _account(clean_db, owner)
    for i in range(3):
        await ledger(clean_db, a, at(i + 1))
    reads = OverviewReads(clean_db)
    page = await reads.feed_page(
        owner,
        None,
        list(FEED_TYPES),
        limit=5,
        cursor=FeedCursor(at(1), 0, 10**9),
        ledger_floor=FLOOR,
    )
    assert page.watermark is None and len(page.events) == 3
