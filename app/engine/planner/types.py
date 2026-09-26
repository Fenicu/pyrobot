from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class Candidate:
    """Рассмотренный вариант: `verdict` — "ok" или причина отказа."""

    scenario: str
    params: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    verdict: str = "ok"


@dataclass(frozen=True, slots=True)
class Act:
    scenario: str
    params: dict[str, Any]
    reason: str
    candidates: tuple[Candidate, ...] = ()


@dataclass(frozen=True, slots=True)
class Wait:
    """Ничего не делать до `until` (None — до следующего события)."""

    until: datetime | None
    reason: str
    candidates: tuple[Candidate, ...] = ()


type Decision = Act | Wait
