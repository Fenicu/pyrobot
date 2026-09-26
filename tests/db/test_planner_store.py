from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.base import Database
from app.db.models import DecisionRow, ScenarioRunRow
from app.db.planner import DbPlannerStore
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
