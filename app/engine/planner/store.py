from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.engine.planner.types import Act, Decision


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    kind: str
    scenario: str | None
    params: dict[str, Any]
    reason: str
    until: datetime | None
    candidates: list[dict[str, Any]]

    @classmethod
    def of(cls, decision: Decision) -> DecisionRecord:
        candidates = [asdict(c) for c in decision.candidates]
        if isinstance(decision, Act):
            return cls(
                "act", decision.scenario, dict(decision.params), decision.reason, None, candidates
            )
        return cls("wait", None, {}, decision.reason, decision.until, candidates)


class PlannerStore(Protocol):
    async def record(self, at: datetime, decision: Decision) -> int: ...

    async def run_started(
        self, decision_id: int, scenario: str, params: Mapping[str, Any], at: datetime
    ) -> int: ...

    async def run_finished(self, run_id: int, status: str, reason: str, at: datetime) -> None: ...

    async def close_running(self, at: datetime) -> int:
        """Незавершённые запуски прошлого процесса → `interrupted`; возвращает их число."""
        ...


@dataclass
class MemoryRun:
    decision_id: int
    scenario: str
    params: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"
    reason: str = ""


@dataclass
class MemoryPlannerStore:
    decisions: list[tuple[datetime, DecisionRecord]] = field(default_factory=list)
    runs: list[MemoryRun] = field(default_factory=list)

    async def record(self, at: datetime, decision: Decision) -> int:
        self.decisions.append((at, DecisionRecord.of(decision)))
        return len(self.decisions)

    async def run_started(
        self, decision_id: int, scenario: str, params: Mapping[str, Any], at: datetime
    ) -> int:
        self.runs.append(MemoryRun(decision_id, scenario, dict(params), at))
        return len(self.runs)

    async def run_finished(self, run_id: int, status: str, reason: str, at: datetime) -> None:
        run = self.runs[run_id - 1]
        run.status, run.reason, run.finished_at = status, reason, at

    async def close_running(self, at: datetime) -> int:
        running = [run for run in self.runs if run.status == "running"]
        for run in running:
            run.status, run.reason, run.finished_at = "interrupted", "restart", at
        return len(running)
