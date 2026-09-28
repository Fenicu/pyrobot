from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


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

# Причины пробуждения планировщика — закрытый набор: незнакомую причину отсечёт mypy.
WakeKind = Literal[
    "busy",
    "cooldown",
    "refresh",
    "sleep_window",
    "sleep_allowed",
    "book_ready",
    "card_ready",
    "fastfood_ready",
    "prizebox_ready",
    "gorbushka_next",
    "gorbushka_comeback",
    "motivation",
    "battle",
    "daily_midnight",
    "daily_reset",
    "stocks_dump",
    "factory_open",
    "tangerine_ready",
    "tangerine_not_player",
    "lottery_open",
    "metro_kick",
    "metro_ready",
]


@dataclass(frozen=True, slots=True)
class Wakeup:
    """Таймер: когда (с запасом) и зачем проснуться; `key` — ключ кулдауна или источник
    обновления."""

    at: datetime
    kind: WakeKind
    key: str | None = None

    @property
    def reason(self) -> str:
        """Строка для `Wait.reason` и журнала: `kind` или `kind:key`."""
        return self.kind if self.key is None else f"{self.kind}:{self.key}"
