from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import Database
from app.db.models import DecisionRow, ScenarioRunRow
from app.engine.fence import Fence
from app.engine.gametime import day_start
from app.engine.planner.store import (
    CLOSED_ON_RESTART,
    DEED_PREFIX,
    EXIT_UNCONFIRMED,
    LAST_DONE,
    NOT_STARTED,
    DecisionRecord,
)
from app.engine.planner.types import Decision


class DbPlannerStore:
    def __init__(self, db: Database, account_id: int, *, fence: Fence | None = None) -> None:
        self._db = db
        self._account_id = account_id
        self._fence = fence

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
            if self._fence is not None:
                await self._fence.guard(session)
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
            if self._fence is not None:
                await self._fence.guard(session)
            session.add(row)
            await session.flush()
            return row.id

    async def run_finished(self, run_id: int, status: str, reason: str, at: datetime) -> None:
        async with self._db.sessions() as session, session.begin():
            if self._fence is not None:
                await self._fence.guard(session)
            await session.execute(
                update(ScenarioRunRow)
                .where(ScenarioRunRow.id == run_id)
                .values(status=status, reason=reason[:200], finished_at=at)
            )

    def _by_key(self, key: str) -> Any:
        return select(ScenarioRunRow.id).where(
            ScenarioRunRow.account_id == self._account_id,
            ScenarioRunRow.idempotency_key == key,
        )

    async def run_by_key(self, key: str) -> int | None:
        async with self._db.sessions() as session:
            run_id = await session.scalar(self._by_key(key))
        return int(run_id) if run_id is not None else None

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
            if self._fence is not None:
                await self._fence.guard(session)
            run_id = await session.scalar(stmt)
            if run_id is not None:
                return int(run_id), True
            existing = await session.scalar(self._by_key(key))
        if existing is None:
            raise RuntimeError(f"scenario run with key {key} vanished")
        return int(existing), False

    async def run_begin(self, run_id: int, at: datetime) -> None:
        async with self._db.sessions() as session, session.begin():
            if self._fence is not None:
                await self._fence.guard(session)
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
            if self._fence is not None:
                await self._fence.guard(session)
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

    async def done_on_day(self, day: date) -> dict[str, int]:
        start = day_start(day)
        query = (
            select(ScenarioRunRow.scenario, func.count())
            .where(
                ScenarioRunRow.account_id == self._account_id,
                ScenarioRunRow.status == "done",
                ScenarioRunRow.scenario.startswith(DEED_PREFIX, autoescape=True),
                ScenarioRunRow.started_at >= start,
                ScenarioRunRow.started_at < start + timedelta(days=1),
            )
            .group_by(ScenarioRunRow.scenario)
        )
        async with self._db.sessions() as session:
            rows = await session.execute(query)
        return {scenario: int(n) for scenario, n in rows.all()}

    async def metro_probes(self, since: datetime) -> list[str]:
        query = (
            select(ScenarioRunRow.params)
            .where(
                ScenarioRunRow.account_id == self._account_id,
                ScenarioRunRow.scenario == "metro",
                ScenarioRunRow.status.not_in(NOT_STARTED),
                ScenarioRunRow.started_at >= since,
            )
            .order_by(ScenarioRunRow.started_at, ScenarioRunRow.id)
        )
        async with self._db.sessions() as session:
            rows: list[dict[str, Any]] = list(await session.scalars(query))
        return [str(params["probe"]) for params in rows if "probe" in params]

    async def metro_stuck_since(self, since: datetime) -> bool:
        query = select(ScenarioRunRow.id).where(
            ScenarioRunRow.account_id == self._account_id,
            ScenarioRunRow.scenario == "metro",
            ScenarioRunRow.reason == EXIT_UNCONFIRMED,
            ScenarioRunRow.finished_at >= since,
        )
        async with self._db.sessions() as session:
            return await session.scalar(query.limit(1)) is not None

    async def runs_on_day(self, scenario: str, day: date) -> int:
        start = day_start(day)
        query = select(func.count()).where(
            ScenarioRunRow.account_id == self._account_id,
            ScenarioRunRow.scenario == scenario,
            ScenarioRunRow.status.not_in(NOT_STARTED),
            ScenarioRunRow.started_at >= start,
            ScenarioRunRow.started_at < start + timedelta(days=1),
        )
        async with self._db.sessions() as session:
            return int(await session.scalar(query) or 0)
