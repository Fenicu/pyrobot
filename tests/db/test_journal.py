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


async def test_revisions_of_message_in_journal_order(clean_db: Database) -> None:
    from dataclasses import replace

    from app.engine.types import Button

    journal = DbJournal(clean_db, account_id=1)
    buttons = (Button("⬆️", 0, 1, data="maze_up"), Button("❤️7", 0, 2, data="maze_first_aid"))
    first = replace(make_msg("🔋88%", msg_id=9), inline=buttons)
    edit = make_msg("Нашёл +2🍔. Котлета - лучшая!", msg_id=9, kind="edit", revision=5)
    other = make_msg("чужое", msg_id=10)
    for msg in (first, other, edit):
        assert await journal.append(msg, [], None, 1) is not None
    revisions = await journal.revisions(first.chat_id, 9)
    assert [m.text for m in revisions] == ["🔋88%", "Нашёл +2🍔. Котлета - лучшая!"]
    assert revisions[0].inline == buttons and revisions[0].button("maze_up") is not None
    assert (revisions[1].kind, revisions[1].revision) == ("edit", 5)
    assert await journal.revisions(first.chat_id, 404) == []


async def test_messages_with_event_give_latest_revision(clean_db: Database) -> None:
    from datetime import timedelta

    from app.engine.parsing.common import Rewards
    from app.engine.parsing.sleep import RobberyAlert, RobberyFight
    from app.engine.types import Button
    from tests.engine.helpers import now

    journal = DbJournal(clean_db, account_id=1)
    wake = (Button("Проснуться", 0, 0, "rob_awake_7"),)
    alert = RobberyAlert(robber="X", level=50)
    fought = RobberyFight(won=True, robber="X", robber_level=50, rewards=Rewards())
    moment = now()
    # Тревога без итога, тревога с итогом-правкой и старая тревога.
    open_alert = make_msg("Опа", msg_id=10, date=moment, buttons=wake)
    await journal.append(open_alert, [alert], None, 1)
    await journal.append(make_msg("Опа", msg_id=11, date=moment, buttons=wake), [alert], None, 1)
    edit = make_msg("Отлично", msg_id=11, kind="edit", revision=5, date=moment)
    await journal.append(edit, [fought], None, 1)
    old = make_msg("Опа", msg_id=12, date=moment - timedelta(minutes=30), buttons=wake)
    await journal.append(old, [alert], None, 1)
    await journal.append(make_msg("другое", msg_id=13, date=moment), [], None, 1)
    found = await journal.messages_with_event(
        open_alert.chat_id, "robbery_alert", moment - timedelta(minutes=10)
    )
    assert [(m.msg_id, m.revision, bool(m.inline)) for m in found] == [
        (10, 0, True),
        (11, 5, False),
    ]


async def test_ledger_rows_in_same_transaction_once(clean_db: Database) -> None:
    from datetime import UTC, date, datetime

    from sqlalchemy import select

    from app.db.models import LedgerRow
    from app.engine.state.ledger import Effect

    journal = DbJournal(clean_db, account_id=1)
    # 21:30 UTC — уже следующие сутки по Москве.
    moment = datetime(2026, 9, 27, 21, 30, tzinfo=UTC)
    msg = make_msg("итог", msg_id=7, date=moment)
    battle = datetime(2026, 9, 27, 15, 30, tzinfo=UTC)
    effects = [
        Effect("deed", {"exp": 158}, {"Пуговица": 1}),
        Effect("deed", {"exp": 47}),
        Effect("factory", {"money": 347}, at=battle),
    ]
    assert await journal.append(msg, [], {"x": 1}, 1, effects=effects) is not None
    assert await journal.append(msg, [], None, 1, effects=effects) is None
    # Та же ревизия с другим содержимым (правки одной секунды) — другой ряд сообщения: его эффекты
    # решает редьюсер, журнал их не отбрасывает.
    same_revision = make_msg("итог!", msg_id=7, date=moment)
    assert await journal.append(same_revision, [], None, 1, effects=effects[:1]) is not None
    async with clean_db.sessions() as session:
        rows = (await session.scalars(select(LedgerRow).order_by(LedgerRow.id))).all()
    assert [(r.kind, r.seq, r.amounts, r.items, r.at, r.day) for r in rows] == [
        ("deed", 0, {"exp": 158}, {"Пуговица": 1}, moment, date(2026, 9, 28)),
        ("deed", 1, {"exp": 47}, {}, moment, date(2026, 9, 28)),
        ("factory", 0, {"money": 347}, {}, battle, date(2026, 9, 27)),
        ("deed", 0, {"exp": 158}, {"Пуговица": 1}, moment, date(2026, 9, 28)),
    ]
    assert {(r.chat_id, r.msg_id, r.revision) for r in rows} == {(msg.chat_id, 7, 0)}
    assert [r.content_hash for r in rows] == [msg.content_hash()] * 3 + [
        same_revision.content_hash()
    ]


async def test_two_lottery_edits_in_one_second_both_in_ledger(clean_db: Database) -> None:
    # Две покупки билета правками одной секунды: ревизия одна (точность — секунда), содержимое
    # разное — два прироста счётчика, два эффекта.
    from sqlalchemy import select

    from app.db.models import LedgerRow
    from tests.engine.test_pipeline import lottery_edits_in_one_second

    journal = DbJournal(clean_db, account_id=1)
    await lottery_edits_in_one_second(journal)
    async with clean_db.sessions() as session:
        rows = (await session.scalars(select(LedgerRow).order_by(LedgerRow.id))).all()
    assert [(r.kind, r.amounts) for r in rows] == [("lottery_tickets", {"money": -30})] * 2
    assert len({r.revision for r in rows}) == 1


async def test_ledger_not_written_without_message_row(clean_db: Database) -> None:
    from sqlalchemy import func, select

    from app.db.models import LedgerRow
    from app.engine.state.ledger import Effect

    journal = DbJournal(clean_db, account_id=1)
    msg = make_msg("итог", msg_id=8)
    await journal.append(msg, [], None, 1)
    assert await journal.append(msg, [], None, 1, effects=[Effect("book", {"exp": 1})]) is None
    async with clean_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(LedgerRow)) == 0


async def test_factory_report_once_for_ledger_lifetime(clean_db: Database) -> None:
    # Постоянный ключ отчёта (вид и день битвы) в БД: второй эффект за ту же битву — не пишется,
    # даже когда редьюсер свой ключ уже забыл.
    from sqlalchemy import select

    from app.db.models import LedgerRow
    from tests.engine.test_pipeline import factory_report_again_after_horizon

    await factory_report_again_after_horizon(DbJournal(clean_db, account_id=1))
    async with clean_db.sessions() as session:
        rows = (await session.scalars(select(LedgerRow).order_by(LedgerRow.id))).all()
    assert [(r.kind, r.day.isoformat(), r.outcome_key) for r in rows] == [
        ("factory", "2026-09-09", "factory:2026-09-09"),
        ("book", "2026-09-27", None),
    ]


async def test_outcome_key_unique_across_messages(clean_db: Database) -> None:
    from sqlalchemy import select

    from app.db.models import LedgerRow
    from app.engine.state.ledger import Effect

    journal = DbJournal(clean_db, account_id=1)
    once = Effect("battle", {"exp": 1}, key="battle:abc")
    plain = Effect("book", {"exp": 5})
    await journal.append(make_msg("a", msg_id=1), [], None, 1, effects=[once, plain])
    await journal.append(make_msg("b", msg_id=2), [], None, 1, effects=[once, plain])
    async with clean_db.sessions() as session:
        rows = (await session.scalars(select(LedgerRow).order_by(LedgerRow.id))).all()
    assert [(r.kind, r.msg_id, r.outcome_key) for r in rows] == [
        ("battle", 1, "battle:abc"),
        ("book", 1, None),
        ("book", 2, None),
    ]
