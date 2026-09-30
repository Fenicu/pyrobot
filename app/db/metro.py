from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.db.base import Database
from app.db.models import MetroRunRow
from app.engine.fence import Fence
from app.engine.metro.store import METRO_HISTORY

# Поля записи забега, которые не легли в отдельные столбцы.
_SUMMARY = (
    "exit",
    "pos",
    "mode",
    "leave_reason",
    "alerts",
    "tokens",
    "message_id",
    "stamina",
    "packs",
)


def _moment(value: Any) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


class DbMetroRunStore:
    def __init__(self, db: Database, account_id: int, *, fence: Fence | None = None) -> None:
        self._db = db
        self._account_id = account_id
        self._fence = fence

    async def save(
        self, scenario_run_id: int | None, status: str, record: Mapping[str, Any]
    ) -> int:
        started = _moment(record.get("started_at"))
        if started is None:
            raise ValueError("metro record without started_at")
        row = MetroRunRow(
            account_id=self._account_id,
            scenario_run_id=scenario_run_id,
            started_at=started,
            finished_at=_moment(record.get("finished_at")),
            status=status,
            outcome=str(record.get("outcome", ""))[:64],
            steps=int(record.get("steps", 0)),
            duration_s=float(record.get("duration_s", 0.0)),
            step_s=record.get("step_s"),
            buffs=list(record.get("buffs", [])),
            result=record.get("result"),
            grid=dict(record.get("grid", {})),
            path=list(record.get("path", [])),
            events=list(record.get("events", [])),
            vitals=list(record.get("vitals", [])),
            summary={k: record[k] for k in _SUMMARY if k in record},
        )
        async with self._db.sessions() as session, session.begin():
            if self._fence is not None:
                await self._fence.guard(session)
            session.add(row)
            await session.flush()
            return row.id

    async def durations(self, limit: int = METRO_HISTORY) -> list[float]:
        query = (
            select(MetroRunRow.duration_s)
            .where(MetroRunRow.account_id == self._account_id, MetroRunRow.status == "done")
            .order_by(MetroRunRow.started_at.desc())
            .limit(limit)
        )
        async with self._db.sessions() as session:
            rows = await session.scalars(query)
            return list(reversed(rows.all()))
