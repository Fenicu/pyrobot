from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.base import Database
from app.db.models import Account, DecisionRow, ScenarioRunRow
from app.db.planner import DbPlannerStore
from app.engine.planner.store import MemoryPlannerStore
from app.engine.planner.types import Act, Candidate, Wait

pytestmark = pytest.mark.db
AT = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


async def test_decisions_and_runs_round_trip(clean_db: Database) -> None:
    store = DbPlannerStore(clean_db, account_id=1)
    candidates = (Candidate("deed:job", {}, 1.6, "chosen"), Candidate("book", {}, None, "busy"))
    act_id = await store.record(AT, Act("deed:job", {}, "best score 1.60", candidates))
    wait_id = await store.record(AT, Wait(AT, "busy", ()))
    run_id = await store.run_started(act_id, "deed:job", {"activity": "job"}, AT)
    await store.run_finished(run_id, "done", "activity_started", AT)
    async with clean_db.sessions() as session:
        act = await session.get(DecisionRow, act_id)
        wait = await session.get(DecisionRow, wait_id)
        run = (await session.scalars(select(ScenarioRunRow))).one()
    assert act is not None and wait is not None
    assert (act.kind, act.scenario, act.until) == ("act", "deed:job", None)
    assert act.candidates[0] == {
        "scenario": "deed:job",
        "params": {},
        "score": 1.6,
        "verdict": "chosen",
    }
    assert (wait.kind, wait.scenario, wait.until) == ("wait", None, AT)
    assert (run.decision_id, run.status, run.reason, run.finished_at) == (
        act_id,
        "done",
        "activity_started",
        AT,
    )


async def test_close_running_interrupts_own_unfinished_runs(clean_db: Database) -> None:
    async with clean_db.engine.begin() as conn:
        await conn.execute(insert(Account).values(id=2).on_conflict_do_nothing())
    mine = DbPlannerStore(clean_db, account_id=1)
    theirs = DbPlannerStore(clean_db, account_id=2)
    decided = await mine.record(AT, Act("book", {}, "book_ready"))
    running = await mine.run_started(decided, "book", {"item": "book"}, AT)
    finished = await mine.run_started(decided, "card", {"item": "card"}, AT)
    await mine.run_finished(finished, "done", "card_used", AT)
    foreign = await theirs.run_started(await theirs.record(AT, Wait(None, "x")), "book", {}, AT)
    later = AT + timedelta(minutes=3)
    assert await mine.close_running(later) == 1
    async with clean_db.sessions() as session:
        rows = await session.scalars(select(ScenarioRunRow))
        runs = {r.id: (r.status, r.reason, r.finished_at) for r in rows}
    assert runs == {
        running: ("interrupted", "restart", later),
        finished: ("done", "card_used", AT),
        foreign: ("running", "", None),
    }


async def test_last_done_per_scenario(clean_db: Database) -> None:
    store = DbPlannerStore(clean_db, account_id=1)
    decided = await store.record(AT, Act("tangerine", {}, "tangerine_ready"))
    first = await store.run_started(decided, "tangerine", {}, AT)
    await store.run_finished(first, "done", "no_error", AT)
    later = AT + timedelta(hours=21)
    second = await store.run_started(decided, "tangerine", {}, later)
    await store.run_finished(second, "done", "no_error", later)
    failed = await store.run_started(decided, "book", {}, later)
    await store.run_finished(failed, "failed", "timeout", later)
    assert await store.last_done() == {"tangerine": later}


async def test_last_done_counts_run_interrupted_by_restart(clean_db: Database) -> None:
    store = DbPlannerStore(clean_db, account_id=1)
    decided = await store.record(AT, Act("tangerine", {}, "tangerine_ready"))
    await store.run_started(decided, "tangerine", {}, AT)
    assert await store.close_running(AT + timedelta(minutes=1)) == 1
    assert await store.last_done() == {"tangerine": AT}


@pytest.mark.parametrize("kind", ["db", "memory"])
async def test_manual_run_queued_then_begun(clean_db: Database, kind: str) -> None:
    store: DbPlannerStore | MemoryPlannerStore = (
        DbPlannerStore(clean_db, account_id=1) if kind == "db" else MemoryPlannerStore()
    )
    run_id, created = await store.run_requested(
        "book", {"item": "book"}, requested={}, key="k1", by="admin", at=AT
    )
    assert created
    again = {"requested": {"x": 1}, "key": "k1", "by": "x", "at": AT}
    assert await store.run_requested("book", {"item": "book"}, **again) == (
        run_id,
        False,
    )
    other, created = await store.run_requested(
        "card", {}, requested={}, key="k2", by="admin", at=AT
    )
    assert created and other != run_id
    later = AT + timedelta(minutes=1)
    await store.run_begin(run_id, later)
    # Рестарт: начатый прерван, так и не начатый — отменён (он точно не исполнялся).
    assert await store.close_running(later) == 2
    assert await store.last_done() == {"book": later}
    if isinstance(store, DbPlannerStore):
        async with clean_db.sessions() as session:
            rows = {r.id: r for r in await session.scalars(select(ScenarioRunRow))}
        begun, queued = rows[run_id], rows[other]
        assert (begun.status, begun.started_at, begun.decision_id) == ("interrupted", later, None)
        assert (begun.requested_by, begun.idempotency_key) == ("admin", "k1")
        assert begun.requested_params == {}
        assert (queued.status, queued.reason) == ("cancelled", "restart")
    else:
        assert [(r.status, r.reason) for r in store.runs] == [
            ("interrupted", "restart"),
            ("cancelled", "restart"),
        ]
