"""Живой кадр забега метро для админки (кадр `metro_live` потока событий): снимок решателя,
бюджет времени и найденное. Кадры — не чаще раза в `LIVE_EVERY_S`; придержанный уходит сам по
истечении интервала, конец забега — всегда."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any

from app.engine.metro.solver import MetroSolver

log = logging.getLogger(__name__)

LIVE = "metro_live"
LIVE_EVERY_S = 1.0
# Событий в кадре — последние; найденное считается по всем.
LIVE_EVENTS = 30
_LOOT = frozenset({"metro_fight", "metro_chest_opened"})
# Экраны выхода показывают всё найденное: их счёт авторитетен, лут после них прибавляется.
_EXITS = frozenset({"metro_exit", "metro_early_exit"})

Frame = Callable[[bool, str | None], dict[str, Any]]
Publish = Callable[[str, dict[str, Any]], None]


def found_of(events: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    found: dict[str, int] = {}

    def add(loot: Mapping[str, Any]) -> None:
        for item, amount in loot.items():
            found[item] = found.get(item, 0) + int(amount)

    for event in events:
        kind = event.get("kind")
        if kind in _EXITS:
            found = {str(k): int(v) for k, v in (event.get("found") or {}).items()}
        elif kind == "metro_loot":
            add({event["item"]: event["amount"]})
        elif kind in _LOOT:
            add(event.get("loot") or {})
    return found


def _moment(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def live_frame(
    solver: MetroSolver,
    *,
    message_id: int,
    scenario_run_id: int | None,
    started: datetime,
    now: datetime,
    running: bool,
    outcome: str | None = None,
) -> dict[str, Any]:
    budget = solver.budget
    snap = solver.snapshot()
    events = list(solver.events)
    return {
        "message_id": message_id,
        "scenario_run_id": scenario_run_id,
        "running": running,
        "started_at": started.isoformat(),
        "battle_at": _moment(budget.battle_at),
        "kick_at": _moment(budget.kick_at()),
        "budget": {
            "total_s": budget.total_s(),
            "used": min(max(budget.used(now), 0.0), 1.0),
            "step_s": solver.step_s(),
        },
        "grid": snap["grid"],
        "pos": snap["pos"],
        "exit": snap["exit"],
        "path": snap["path"],
        "vitals": list(solver.vitals),
        "events": events[-LIVE_EVENTS:],
        "steps": snap["steps"],
        "mode": snap["mode"],
        "leave_reason": snap["leave_reason"],
        "stamina": snap["stamina"],
        "packs": snap["packs"],
        "found": found_of(events),
        "last_event": events[-1] if events else None,
        "outcome": outcome,
    }


class LiveFeed:
    """Троттлинг кадров: `update` — кадр идущего забега, `close` — последний, с исходом.
    Сбой публикации забег не останавливает."""

    def __init__(
        self,
        publish: Publish,
        frame: Frame,
        monotonic: Callable[[], float],
        every_s: float = LIVE_EVERY_S,
    ) -> None:
        self._publish = publish
        self._frame = frame
        self._monotonic = monotonic
        self._every_s = every_s
        self._sent_at: float | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._closed = False

    def update(self) -> None:
        if self._closed or self._timer is not None:
            return
        wait = 0.0 if self._sent_at is None else self._sent_at + self._every_s - self._monotonic()
        if wait <= 0:
            self._send(True, None)
            return
        self._timer = asyncio.get_running_loop().call_later(wait, self._flush)

    def close(self, outcome: str) -> None:
        if self._closed:
            return
        self._cancel()
        self._closed = True
        self._send(False, outcome)

    def _flush(self) -> None:
        self._timer = None
        self._send(True, None)

    def _cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _send(self, running: bool, outcome: str | None) -> None:
        self._sent_at = self._monotonic()
        try:
            self._publish(LIVE, self._frame(running, outcome))
        except Exception:
            log.exception("metro live frame not published")
