from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from app.engine.gametime import tasks_day
from app.engine.planner.types import Act, Decision

# Запуски, после которых сценарий мог исполниться: мандарин после рестарта не повторяется.
LAST_DONE = ("done", "interrupted")
# Счётчик дел за день: только успешные запуски `deed:*`.
DEED_PREFIX = "deed:"
# Незавершённые запуски прошлого процесса: начатый мог исполниться, из очереди — точно нет.
CLOSED_ON_RESTART = {"running": "interrupted", "queued": "cancelled"}
# Запуски из очереди, которые так и не начались, и запуски, которые подавил kill switch или
# остановка: команда реально не ушла, в дневной бюджет не считаются.
NOT_STARTED = ("queued", "cancelled", "suppressed")
# Остановка забега: после итога игра не ответила ни на /compact, ни на /main.
EXIT_UNCONFIRMED = "exit_unconfirmed"


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
        """Ручной запуск в очереди (`queued`): `params` — с чем он исполнится, `requested` — что
        прислал клиент (по нему сверяется повтор ключа). Ключ уже встречался — (его запуск,
        False)."""
        ...

    async def run_begin(self, run_id: int, at: datetime) -> None:
        """Запуск из очереди начал исполняться (`running`, начало — `at`)."""
        ...

    async def close_running(self, at: datetime) -> int:
        """Незавершённые запуски прошлого процесса → `interrupted`, так и не начатые из очереди
        → `cancelled`; возвращает их число."""
        ...

    async def last_done(self) -> dict[str, datetime]:
        """Начало последнего успешного или прерванного рестартом (исход неизвестен) запуска."""
        ...

    async def done_on_day(self, day: date) -> dict[str, int]:
        """Число успешных (`done`) запусков каждого дела `deed:*`, начатых в день заданий `day`
        (граница — 00:00 MSK)."""
        ...

    async def runs_on_day(self, scenario: str, day: date) -> int:
        """Число запусков сценария, начатых в день заданий `day`, с любым исходом (кроме так и не
        начатых из очереди)."""
        ...

    async def metro_probes(self, since: datetime) -> list[str]:
        """Проверки выхода из метро (`params.probe` запусков `metro`), начатые не раньше `since`,
        по порядку: с любым исходом, кроме так и не ушедших."""
        ...

    async def metro_stuck_since(self, since: datetime) -> bool:
        """Забег `metro`, закончившийся не раньше `since` остановкой `exit_unconfirmed`: после
        итога игра не ответила ни на `/compact`, ни на `/main`."""
        ...


@dataclass
class MemoryRun:
    decision_id: int | None
    scenario: str
    params: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"
    reason: str = ""
    requested_by: str | None = None
    key: str | None = None
    requested: dict[str, Any] | None = None


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
        for run_id, run in enumerate(self.runs, start=1):
            if run.key == key:
                return run_id, False
        self.runs.append(
            MemoryRun(
                None,
                scenario,
                dict(params),
                at,
                status="queued",
                requested_by=by,
                key=key,
                requested=dict(requested),
            )
        )
        return len(self.runs), True

    async def run_begin(self, run_id: int, at: datetime) -> None:
        run = self.runs[run_id - 1]
        run.status, run.started_at = "running", at

    async def close_running(self, at: datetime) -> int:
        closed = 0
        for run in self.runs:
            if run.status in CLOSED_ON_RESTART:
                run.status, run.reason, run.finished_at = (
                    CLOSED_ON_RESTART[run.status],
                    "restart",
                    at,
                )
                closed += 1
        return closed

    async def last_done(self) -> dict[str, datetime]:
        done: dict[str, datetime] = {}
        for run in self.runs:
            if run.status in LAST_DONE:
                done[run.scenario] = max(run.started_at, done.get(run.scenario, run.started_at))
        return done

    async def done_on_day(self, day: date) -> dict[str, int]:
        counts: dict[str, int] = {}
        for run in self.runs:
            if run.status != "done" or not run.scenario.startswith(DEED_PREFIX):
                continue
            if tasks_day(run.started_at) == day:
                counts[run.scenario] = counts.get(run.scenario, 0) + 1
        return counts

    async def runs_on_day(self, scenario: str, day: date) -> int:
        return sum(
            1
            for run in self.runs
            if run.scenario == scenario
            and run.status not in NOT_STARTED
            and tasks_day(run.started_at) == day
        )

    async def metro_probes(self, since: datetime) -> list[str]:
        runs = sorted(
            (
                run
                for run in self.runs
                if run.scenario == "metro"
                and "probe" in run.params
                and run.status not in NOT_STARTED
                and run.started_at >= since
            ),
            key=lambda run: run.started_at,
        )
        return [str(run.params["probe"]) for run in runs]

    async def metro_stuck_since(self, since: datetime) -> bool:
        return any(
            run.scenario == "metro"
            and run.reason == EXIT_UNCONFIRMED
            and run.finished_at is not None
            and run.finished_at >= since
            for run in self.runs
        )
