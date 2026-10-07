import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.engine.metro.budget import Budget
from app.engine.metro.live import LIVE_EVENTS, LiveFeed, found_of, live_frame
from app.engine.metro.solver import MetroSolver, policy_of
from app.engine.settings import MetroSection

T0 = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)


class Ticks:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


class Sink:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, type_: str, data: dict[str, Any]) -> None:
        self.frames.append((type_, data))


def _frame(running: bool, outcome: str | None) -> dict[str, Any]:
    return {"running": running, "outcome": outcome}


def test_found_sums_loot_fights_and_chests() -> None:
    events = [
        {"kind": "metro_loot", "item": "money", "amount": 30},
        {"kind": "metro_fight", "won": True, "loot": {"money": 71, "gears": 9}},
        {"kind": "metro_chest_opened", "result": "stash", "loot": {"gears": 2}},
        {"kind": "metro_chest_opened", "result": "arrow", "loot": {}},
        {"kind": "metro_npc", "strength": "low"},
    ]
    assert found_of(events) == {"money": 101, "gears": 11}


def test_found_exit_screen_is_authoritative_then_adds_later_loot() -> None:
    events = [
        {"kind": "metro_loot", "item": "money", "amount": 30},
        {"kind": "metro_exit", "found": {"money": 157, "burger": 1}},
        {"kind": "metro_loot", "item": "money", "amount": 5},
        {"kind": "metro_finished", "loot": {"money": 162, "burger": 1}},
    ]
    assert found_of(events) == {"money": 162, "burger": 1}
    early = [*events, {"kind": "metro_early_exit", "found": {"money": 170}, "half": {"money": 85}}]
    assert found_of(early) == {"money": 170}


def test_frame_carries_budget_last_events_and_found() -> None:
    budget = Budget(started=T0, battle_at=T0 + timedelta(minutes=60), margin=timedelta(minutes=20))
    solver = MetroSolver(policy_of(MetroSection()), budget)
    for n in range(LIVE_EVENTS + 5):
        solver.events.append(
            {"step": n, "pos": [0, 0], "kind": "metro_loot", "item": "money", "amount": 1}
        )
    frame = live_frame(
        solver,
        message_id=77,
        scenario_run_id=5,
        started=T0,
        now=T0 + timedelta(minutes=30),
        running=True,
    )
    assert frame["message_id"] == 77 and frame["scenario_run_id"] == 5
    assert (frame["running"], frame["outcome"]) == (True, None)
    assert frame["started_at"] == T0.isoformat()
    assert frame["battle_at"] == (T0 + timedelta(minutes=60)).isoformat()
    assert frame["kick_at"] == (T0 + timedelta(minutes=45)).isoformat()
    assert frame["budget"] == {"total_s": 2400.0, "used": 0.75, "step_s": 5.0}
    assert len(frame["events"]) == LIVE_EVENTS and frame["events"][-1]["step"] == LIVE_EVENTS + 4
    assert frame["last_event"] == frame["events"][-1]
    # Найденное — по всем событиям забега, а не только по последним в кадре.
    assert frame["found"] == {"money": LIVE_EVENTS + 5}
    assert (frame["pos"], frame["exit"], frame["steps"]) == ([0, 0], None, 0)
    assert frame["path"] == [[0, 0]]
    assert frame["grid"] == {"cells": {}, "visited": []}
    # Кадр не меняется вместе с решателем: он хранится как последний.
    solver.vitals.append({"step": 1})
    solver.events.append({"kind": "metro_npc"})
    assert frame["vitals"] == [] and len(frame["events"]) == LIVE_EVENTS
    late = live_frame(
        solver,
        message_id=77,
        scenario_run_id=None,
        started=T0,
        now=T0 + timedelta(hours=2),
        running=False,
        outcome="finished",
    )
    assert late["budget"]["used"] == 1.0
    assert (late["running"], late["outcome"]) == (False, "finished")


def test_frame_without_battle_has_no_budget_total() -> None:
    solver = MetroSolver(
        policy_of(MetroSection()), Budget(started=T0, battle_at=None, margin=timedelta(0))
    )
    frame = live_frame(
        solver, message_id=1, scenario_run_id=None, started=T0, now=T0, running=True
    )
    assert (frame["battle_at"], frame["kick_at"]) == (None, None)
    assert frame["budget"] == {"total_s": None, "used": 0.0, "step_s": 5.0}
    assert (frame["found"], frame["last_event"]) == ({}, None)


async def test_feed_throttles_to_one_frame_per_second_and_sends_the_last() -> None:
    clock, sink = Ticks(), Sink()
    built: list[int] = []

    def frame(running: bool, outcome: str | None) -> dict[str, Any]:
        built.append(len(built))
        return {"n": built[-1], "running": running, "outcome": outcome}

    feed = LiveFeed(sink, frame, clock, every_s=0.2)
    feed.update()
    clock.t += 0.05
    feed.update()
    clock.t += 0.05
    feed.update()
    # Первый кадр — сразу, следующие в пределах интервала — нет.
    assert [d["n"] for _, d in sink.frames] == [0]
    clock.t += 0.2
    await asyncio.sleep(0.3)
    # Отложенный кадр уходит сам по истечении интервала — свежий, на момент отправки.
    assert [d["n"] for _, d in sink.frames] == [0, 1]
    clock.t += 0.25
    feed.update()
    assert [d["n"] for _, d in sink.frames] == [0, 1, 2]
    clock.t += 0.01
    feed.update()
    feed.close("finished")
    # Конец — всегда, и отложенный кадр после него уже не уходит.
    await asyncio.sleep(0.3)
    assert [t for t, _ in sink.frames] == ["metro_live"] * 4
    assert sink.frames[-1][1]["running"] is False and sink.frames[-1][1]["outcome"] == "finished"
    feed.update()
    assert len(sink.frames) == 4


def test_feed_publish_failure_does_not_break_the_run() -> None:
    def broken(type_: str, data: dict[str, Any]) -> None:
        raise RuntimeError("stream down")

    feed = LiveFeed(broken, _frame, Ticks())
    feed.update()
    feed.close("finished")
