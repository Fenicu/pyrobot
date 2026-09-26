from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import update

from app.db.base import Database
from app.db.models import DecisionRow, ScenarioRunRow
from app.engine.planner.store import DecisionRecord
from app.engine.planner.types import Decision


class DbPlannerStore:
    def __init__(self, db: Database, account_id: int) -> None:
        self._db = db
        self._account_id = account_id

    async def record(self, at: datetime, decision: Decision) -> int:
        rec = DecisionRecord.of(decision)
        row = DecisionRow(
            account_id=self._account_id,
            at=at,
            kind=rec.kind,
            scenario=rec.scenario,
            params=rec.params,
            reason=rec.reason[:200],
            until=rec.until,
            candidates=rec.candidates,
        )
        async with self._db.sessions() as session, session.begin():
            session.add(row)
            await session.flush()
            return row.id

    async def run_started(
        self, decision_id: int, scenario: str, params: Mapping[str, Any], at: datetime
    ) -> int:
        row = ScenarioRunRow(
            account_id=self._account_id,
            decision_id=decision_id,
            scenario=scenario,
            params=dict(params),
            started_at=at,
            status="running",
        )
        async with self._db.sessions() as session, session.begin():
            session.add(row)
            await session.flush()
            return row.id

    async def run_finished(self, run_id: int, status: str, reason: str, at: datetime) -> None:
        async with self._db.sessions() as session, session.begin():
            await session.execute(
                update(ScenarioRunRow)
                .where(ScenarioRunRow.id == run_id)
                .values(status=status, reason=reason[:200], finished_at=at)
            )

    async def close_running(self, at: datetime) -> int:
        async with self._db.sessions() as session, session.begin():
            closed = await session.scalars(
                update(ScenarioRunRow)
                .where(
                    ScenarioRunRow.account_id == self._account_id,
                    ScenarioRunRow.status == "running",
                )
                .values(status="interrupted", reason="restart", finished_at=at)
                .returning(ScenarioRunRow.id)
            )
            return len(closed.all())
