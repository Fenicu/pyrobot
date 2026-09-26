from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.db.base import Database
from app.db.metro import DbMetroRunStore
from app.db.models import MetroRunRow
from app.db.planner import DbPlannerStore
from app.engine.planner.types import Act

pytestmark = pytest.mark.db
AT = datetime(2026, 9, 26, 22, 12, tzinfo=UTC)


def record(started: datetime, duration_s: float, outcome: str = "finished") -> dict[str, Any]:
    return {
        "grid": {"cells": {"0,0": ".", "0,1": "E"}, "visited": [[0, 0], [0, 1]]},
        "pos": [0, 1],
        "exit": [0, 1],
        "steps": 1,
        "mode": "leave",
        "leave_reason": "explored",
        "path": [[0, 0], [0, 1]],
        "events": [{"step": 1, "pos": [0, 1], "kind": "metro_exit", "found": {"money": 5}}],
        "vitals": [{"step": 0, "pos": [0, 0], "stamina": 88, "packs": 7}],
        "alerts": [],
        "stamina": 100,
        "packs": 7,
        "message_id": 3624441,
        "buffs": ["fastMove", "strong", "firstAid"],
        "tokens": 9993,
        "started_at": started.isoformat(),
        "finished_at": (started + timedelta(seconds=duration_s)).isoformat(),
        "duration_s": duration_s,
        "step_s": 3.9,
        "result": {"money": 5},
        "outcome": outcome,
    }


async def test_metro_run_round_trip(clean_db: Database) -> None:
    planner = DbPlannerStore(clean_db, account_id=1)
    decided = await planner.record(AT, Act("metro", {}, "metro_ready"))
    run_id = await planner.run_started(decided, "metro", {}, AT)
    store = DbMetroRunStore(clean_db, account_id=1)
    saved = await store.save(run_id, "done", record(AT, 960.0))
    async with clean_db.sessions() as session:
        row = (await session.scalars(select(MetroRunRow))).one()
    assert row.id == saved and row.scenario_run_id == run_id
    assert (row.status, row.outcome, row.steps, row.duration_s) == ("done", "finished", 1, 960.0)
    assert row.finished_at == AT + timedelta(seconds=960)
    assert row.grid["visited"] == [[0, 0], [0, 1]] and row.path == [[0, 0], [0, 1]]
    assert row.events[0]["kind"] == "metro_exit" and row.vitals[0]["packs"] == 7
    assert row.buffs == ["fastMove", "strong", "firstAid"] and row.result == {"money": 5}
    assert row.summary["exit"] == [0, 1] and row.summary["leave_reason"] == "explored"


async def test_durations_of_finished_runs_oldest_first(clean_db: Database) -> None:
    store = DbMetroRunStore(clean_db, account_id=1)
    await store.save(None, "done", record(AT, 900.0))
    await store.save(None, "stopped", record(AT + timedelta(hours=16), 100.0, "paused"))
    await store.save(None, "done", record(AT + timedelta(hours=32), 1200.0))
    assert await store.durations() == [900.0, 1200.0]
    assert await store.durations(limit=1) == [1200.0]
