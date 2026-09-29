from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db.base import Database
from app.db.journal import DbJournal
from app.db.metro import DbMetroRunStore
from app.db.models import (
    ActionRow,
    DecisionRow,
    LedgerRow,
    MessageRow,
    MetricRow,
    MetroRunRow,
    NotificationRow,
    ScenarioRunRow,
    UnrecognizedRow,
)
from app.db.planner import DbPlannerStore
from app.db.retention import DbRetention
from app.engine.events import Unrecognized
from app.engine.gametime import MSK
from app.engine.metro.store import METRO_HISTORY
from app.engine.planner.types import Wait
from app.engine.settings import RetentionSection
from tests.engine.helpers import make_msg

pytestmark = pytest.mark.db
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def _action(
    days: float,
    status: str,
    cls: str = "action",
    reconciled: bool = False,
    key: str | None = None,
) -> ActionRow:
    return ActionRow(
        account_id=1,
        created_at=_ago(days),
        kind="send",
        chat_id=1,
        payload={"text": "/job"},
        command_class=cls,
        status=status,
        reason="",
        reconciled_at=_ago(days) if reconciled else None,
        idempotency_key=key,
        source="manual" if key else "planner",
    )


async def _count(db: Database, model: type) -> int:
    async with db.sessions() as s:
        return int(await s.scalar(select(func.count()).select_from(model)) or 0)


async def test_retention_defaults() -> None:
    policy = RetentionSection()
    assert (policy.messages_days, policy.decisions_days, policy.metrics_days) == (90, 30, 365)


async def test_purge_with_longest_retention(clean_db: Database) -> None:
    # Предел хранения — 10 лет: срок считается без переполнения даты.
    longest = RetentionSection(
        messages_days=3650, decisions_days=3650, metrics_days=3650, ledger_days=3650
    )
    purged = await DbRetention(clean_db, 1).purge(NOW, longest)
    assert set(purged.values()) == {0}


async def test_purge_keeps_recent_and_open_obligations(clean_db: Database) -> None:
    journal = DbJournal(clean_db, 1)
    for i, days in enumerate((91, 91, 89)):
        await journal.append(
            make_msg(f"m{i}", msg_id=i, received_at=_ago(days)),
            [Unrecognized(first_line=f"m{i}")],
            None,
            0,
        )
    planner = DbPlannerStore(clean_db, 1)
    old_decision = await planner.record(_ago(31), Wait(None, "old"))
    await planner.record(_ago(29), Wait(None, "fresh"))
    done_run = await planner.run_started(old_decision, "book", {}, _ago(91))
    await planner.run_finished(done_run, "done", "", _ago(91))
    await planner.run_started(old_decision, "book", {}, _ago(91))
    manual_run, _ = await planner.run_requested(
        "book", {"item": "book"}, requested={}, key="old-key", by="admin", at=_ago(91)
    )
    await planner.run_finished(manual_run, "done", "", _ago(91))
    metro = DbMetroRunStore(clean_db, 1)
    await metro.save(None, "stopped", {"started_at": _ago(366).isoformat()})
    await metro.save(None, "done", {"started_at": _ago(10).isoformat()})
    async with clean_db.sessions() as s, s.begin():
        s.add_all(
            [
                _action(91, "confirmed"),
                _action(91, "outcome_unknown"),
                _action(91, "outcome_unknown", reconciled=True),
                _action(91, "outcome_unknown", cls="nav"),
                _action(91, "outcome_unknown", cls="forward"),
                _action(91, "suppressed", key="manual:old"),
                _action(1, "confirmed"),
                MetricRow(account_id=1, ts=_ago(366), key="money", value=1),
                MetricRow(account_id=1, ts=_ago(1), key="money", value=2),
                NotificationRow(
                    account_id=1, created_at=_ago(91), level="info", code="a", text=""
                ),
                NotificationRow(account_id=1, created_at=_ago(1), level="info", code="b", text=""),
            ]
        )
    purged = await DbRetention(clean_db, 1, batch=1).purge(NOW, RetentionSection())
    assert purged == {
        "messages": 2,
        "actions": 4,
        "scenario_runs": 1,
        "notifications": 1,
        "decisions": 1,
        "metrics": 1,
        "metro_runs": 1,
        "ledger": 0,
    }
    assert await _count(clean_db, MessageRow) == 1
    # Нераспознанные уходят каскадом вместе с сообщениями.
    assert await _count(clean_db, UnrecognizedRow) == 1
    async with clean_db.sessions() as s:
        left = {
            (a.status, a.command_class, a.reconciled_at is None)
            for a in await s.scalars(select(ActionRow))
        }
        runs = sorted(r.status for r in await s.scalars(select(ScenarioRunRow)))
        decisions = [d.reason for d in await s.scalars(select(DecisionRow))]
    # Несверенный исход траты — обязательство сверки, его не удалить; ручные с ключом — журнал
    # ключей идемпотентности, их тоже. Неизвестный исход навигации и пересылки не сверяется.
    assert left == {
        ("outcome_unknown", "action", True),
        ("confirmed", "action", True),
        ("suppressed", "action", True),
    }
    assert runs == ["done", "running"] and decisions == ["fresh"]
    assert await _count(clean_db, MetroRunRow) == 1 and await _count(clean_db, MetricRow) == 1
    assert await _count(clean_db, NotificationRow) == 1
    again = await DbRetention(clean_db, 1).purge(NOW, RetentionSection())
    assert set(again.values()) == {0}


def _ledger(at: datetime, day: date) -> LedgerRow:
    return LedgerRow(
        account_id=1,
        at=at,
        recorded_at=at,
        day=day,
        kind="book",
        amounts={"exp": 1},
        items={},
        chat_id=1,
        msg_id=int(at.timestamp()),
        revision=0,
        content_hash="h",
        seq=0,
    )


async def test_ledger_purged_by_whole_msk_days(clean_db: Database) -> None:
    # NOW — 27.09 15:00 MSK: хранятся сегодня и 30 полных суток до него (с 28.08).
    first_kept = datetime(2026, 8, 28, 0, 1, tzinfo=MSK)
    last_gone = datetime(2026, 8, 27, 23, 59, tzinfo=MSK)
    async with clean_db.sessions() as s, s.begin():
        s.add_all([_ledger(first_kept, date(2026, 8, 28)), _ledger(last_gone, date(2026, 8, 27))])
    purged = await DbRetention(clean_db, 1).purge(NOW, RetentionSection())
    assert purged["ledger"] == 1
    async with clean_db.sessions() as s:
        assert [r.day for r in await s.scalars(select(LedgerRow))] == [date(2026, 8, 28)]


def test_ledger_retention_keeps_every_shown_day() -> None:
    assert RetentionSection().ledger_days == 31
    with pytest.raises(ValidationError):
        RetentionSection(ledger_days=30)


async def test_purge_keeps_last_completed_metro_runs(clean_db: Database) -> None:
    metro = DbMetroRunStore(clean_db, 1)
    for i in range(METRO_HISTORY + 2):
        await metro.save(None, "done", {"started_at": _ago(1000 - i).isoformat(), "duration_s": i})
    await metro.save(None, "stopped", {"started_at": _ago(500).isoformat()})
    purged = await DbRetention(clean_db, 1).purge(NOW, RetentionSection())
    # Для p90 бюджета остаются последние 20 завершённых забегов, даже старше года.
    assert purged["metro_runs"] == 3
    assert await metro.durations() == [float(i) for i in range(2, METRO_HISTORY + 2)]


async def test_purged_run_leaves_open_obligation_without_run(clean_db: Database) -> None:
    # Шаг запуска с несверенным исходом живёт до сверки, а сам запуск удаляется по сроку:
    # ссылка на запуск обнуляется, удаление не падает на внешнем ключе.
    planner = DbPlannerStore(clean_db, 1)
    decision = await planner.record(_ago(10), Wait(None, "old"))
    run_id = await planner.run_started(decision, "deed:job", {}, _ago(91))
    await planner.run_finished(run_id, "failed", "timeout", _ago(91))
    step = _action(91, "outcome_unknown")
    step.scenario_run_id = run_id
    async with clean_db.sessions() as s, s.begin():
        s.add(step)
    purged = await DbRetention(clean_db, 1).purge(NOW, RetentionSection())
    assert (purged["scenario_runs"], purged["actions"]) == (1, 0)
    async with clean_db.sessions() as s:
        kept = await s.get(ActionRow, step.id)
    assert kept is not None and kept.scenario_run_id is None
