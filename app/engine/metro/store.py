from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

# Бюджет метро — по p90 последних забегов.
METRO_HISTORY = 20


class MetroRunStore(Protocol):
    async def save(
        self, scenario_run_id: int | None, status: str, record: Mapping[str, Any]
    ) -> int:
        """Сохраняет запись забега (`ScenarioResult.details["metro"]`)."""
        ...

    async def durations(self, limit: int = METRO_HISTORY) -> list[float]:
        """Длительности последних завершённых забегов в секундах, от старых к новым."""
        ...


@dataclass
class MemoryMetroRunStore:
    runs: list[dict[str, Any]] = field(default_factory=list)

    async def save(
        self, scenario_run_id: int | None, status: str, record: Mapping[str, Any]
    ) -> int:
        self.runs.append({**record, "scenario_run_id": scenario_run_id, "status": status})
        return len(self.runs)

    async def durations(self, limit: int = METRO_HISTORY) -> list[float]:
        done = [float(r["duration_s"]) for r in self.runs if r["status"] == "done"]
        return done[-limit:]
