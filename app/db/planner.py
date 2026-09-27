from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import Database
from app.db.models import DecisionRow, ScenarioRunRow
from app.engine.planner.store import CLOSED_ON_RESTART, LAST_DONE, DecisionRecord
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

    async def run_requested(
        self,
        scenario: str,
        params: Mapping[str, Any],
        *,
        requested: Mapping[str, Any],
        key: str,
        by: str,
        at: datetime,
    ) -> tuple[int, bool]:
        stmt = (
            pg_insert(ScenarioRunRow)
            .values(
                account_id=self._account_id,
                scenario=scenario,
                params=dict(params),
                started_at=at,
                status="queued",
                reason="",
                requested_by=by,
                idempotency_key=key,
                requested_params=dict(requested),
            )
            .on_conflict_do_nothing(constraint="uq_scenario_runs_account_key")
            .returning(ScenarioRunRow.id)
        )
        async with self._db.sessions() as session, session.begin():
            run_id = await session.scalar(stmt)
            if run_id is not None:
                return int(run_id), True
            existing = await session.scalar(
                select(ScenarioRunRow.id).where(
                    ScenarioRunRow.account_id == self._account_id,
                    ScenarioRunRow.idempotency_key == key,
                )
            )
        if existing is None:
            raise RuntimeError(f"scenario run with key {key} vanished")
        return int(existing), False

    async def run_begin(self, run_id: int, at: datetime) -> None:
        async with self._db.sessions() as session, session.begin():
            await session.execute(
                update(ScenarioRunRow)
                .where(ScenarioRunRow.id == run_id)
                .values(status="running", started_at=at)
            )

    async def close_running(self, at: datetime) -> int:
        status = case(
            *((ScenarioRunRow.status == old, new) for old, new in CLOSED_ON_RESTART.items())
        )
        async with self._db.sessions() as session, session.begin():
            closed = await session.scalars(
                update(ScenarioRunRow)
                .where(
                    ScenarioRunRow.account_id == self._account_id,
                    ScenarioRunRow.status.in_(CLOSED_ON_RESTART),
                )
                .values(status=status, reason="restart", finished_at=at)
                .returning(ScenarioRunRow.id)
            )
            return len(closed.all())

    async def last_done(self) -> dict[str, datetime]:
        query = (
            select(ScenarioRunRow.scenario, func.max(ScenarioRunRow.started_at))
            .where(
                ScenarioRunRow.account_id == self._account_id,
                ScenarioRunRow.status.in_(LAST_DONE),
            )
            .group_by(ScenarioRunRow.scenario)
        )
        async with self._db.sessions() as session:
            rows = await session.execute(query)
        return {scenario: started for scenario, started in rows.all()}
