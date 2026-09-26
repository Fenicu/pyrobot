from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.engine.events import Event
from app.engine.metro.budget import FRONTIER_AT, LATE_AT, RECENT_MOVES, Budget
from app.engine.metro.grid import Grid, Pos, step
from app.engine.metro.plan import Mode, exit_route, explore_step, reach, reachable_exit, targets
from app.engine.parsing.metro import (
    EXIT,
    FLOOR,
    MetroChest,
    MetroChestOpened,
    MetroEarlyExit,
    MetroExit,
    MetroFight,
    MetroFinished,
    MetroFirstAid,
    MetroLoot,
    MetroMap,
    MetroNpc,
)

FULL = 100
# Выход неизвестен или не успеть к нему до выброса игрой: за минуту до выброса выходим сами
# кнопкой 🚪 — та же половина найденного, но экран досрочного выхода известен.
EARLY_EXIT_LEAD = timedelta(minutes=1)
# Один клик без хода: темп шлюза 1.6 с и ответ игры.
CLICK_S = 2.0


@dataclass(frozen=True, slots=True)
class Policy:
    """Политики событий (настройки `metro`)."""

    heal_at: int = 50
    heal_before_exit: bool = True
    chest_min_packs: int = 2
    npc_low: bool = True
    npc_high: bool = False
    # Без аптечек не драться при 🔋 ниже этого: лечиться на экране NPC нельзя.
    npc_min_stamina: int = 30


@dataclass(frozen=True, slots=True)
class Click:
    data: str
    reason: str


@dataclass(frozen=True, slots=True)
class Done:
    reason: str


@dataclass(frozen=True, slots=True)
class Halt:
    """Дальше без человека нельзя: незнакомый экран, потерянная позиция, нет пути."""

    reason: str


Move = Click | Done | Halt

# Экраны, на которых «Продолжить» — единственная и известная кнопка.
_CONTINUE: dict[type[Event], str] = {
    MetroLoot: "loot",
    MetroFight: "fight",
    MetroChestOpened: "chest_opened",
}
# Экраны, которые показываются на клетке, куда пришёл ход.
_ON_CELL = (MetroLoot, MetroNpc, MetroChest, MetroExit)


@dataclass
class MetroSolver:
    """Решатель забега: вливает кадры в карту и выбирает следующий клик. Время — аргументом."""

    policy: Policy
    budget: Budget
    grid: Grid = field(default_factory=Grid)
    pos: Pos = (0, 0)
    exit_at: Pos | None = None
    stamina: int | None = None
    packs: int | None = None
    steps: int = 0
    mode: Mode = "explore"
    leave_reason: str | None = None
    lost: bool = False
    path: list[Pos] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    vitals: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    result: dict[str, int] | None = None
    # Длительности недавних ходов «клик → новый кадр», с.
    move_times: list[float] = field(default_factory=list)
    _pending: str | None = None
    _heal: bool = False
    _early: bool = False
    _far: bool = False
    _move_sent_at: datetime | None = None
    _last: str | None = None
    _screen: Event | None = None

    def __post_init__(self) -> None:
        if not self.path:
            self.path = [self.pos]

    # --- наблюдение

    def observe(self, event: Event) -> None:
        self._screen = event
        if isinstance(event, MetroMap):
            self._on_map(event)
            return
        if isinstance(event, _ON_CELL) and self._pending is not None:
            self._arrive(self._pending)
            self.grid.visited.add(self.pos)
        if isinstance(event, MetroExit):
            self.grid.cells[self.pos] = EXIT
            self.exit_at = self.pos
        elif isinstance(event, _ON_CELL) and self.grid.get(self.pos) is None:
            self.grid.cells[self.pos] = FLOOR
        if isinstance(event, MetroFight) and event.stamina is not None:
            self.stamina = event.stamina
        if isinstance(event, MetroChestOpened) and event.result == "arrow":
            self.stamina = 0
        if isinstance(event, MetroFirstAid):
            self.stamina, self.packs = event.stamina, event.packs
        if isinstance(event, MetroFinished):
            self.result = dict(event.loot)
            self.stamina = event.stamina
        self._note(event)

    def _arrive(self, direction: str) -> None:
        self.pos = step(self.pos, direction)
        self.steps += 1
        self.path.append(self.pos)
        self._last = direction
        self._pending = None

    def cancel(self) -> None:
        """Решённый ход не отправлен (экран сменился): не ждать его, решение — заново."""
        self._pending = None
        self._move_sent_at = None
        self._early = False

    def resync(self) -> None:
        """Следующий кадр — свежий после рестарта: позиция ищется по всей карте, пока окно не
        сольётся с ней."""
        self._far = True

    def _on_map(self, frame: MetroMap) -> None:
        if frame.footer == "going":
            # Окно ещё старое: только запоминаем ход (при воспроизведении записи).
            self._pending = frame.direction
            return
        if frame.footer == "arrived" and frame.direction is not None:
            self._arrive(frame.direction)
        elif frame.footer == "wall":
            self._note_kind("wall", direction=self._pending)
        self._pending = None
        if self.grid.conflicts(frame.window, self.pos):
            located = self.grid.locate(frame.window, self.pos, far=self._far)
            if located is None:
                self.lost = True
                self._note_kind("lost", window=list(frame.window))
                return
            self._note_kind("relocated", to=list(located))
            self.pos = located
            self.path.append(located)
        self.lost = False
        self._far = False
        self.grid.merge(frame.window, self.pos)
        if self.exit_at is None and (exits := self.grid.exits()):
            self.exit_at = reachable_exit(self.grid, self.pos) or exits[0]
        self.stamina = frame.stamina
        if frame.packs is not None:
            self.packs = frame.packs
        vitals = {"stamina": self.stamina, "packs": self.packs}
        self.vitals.append({"step": self.steps, "pos": list(self.pos), **vitals})

    def _note(self, event: Event) -> None:
        if isinstance(event, (MetroMap, MetroFirstAid)):
            return
        data = event.to_json()
        data.pop("kind", None)
        self._note_kind(event.kind, **data)

    def _note_kind(self, kind: str, **detail: Any) -> None:
        self.events.append({"step": self.steps, "pos": list(self.pos), "kind": kind, **detail})

    # --- решение

    def decide(self, now: datetime) -> Move:
        screen = self._screen
        if isinstance(screen, MetroMap):
            if screen.footer == "going":
                return Halt("moving")
            return self._on_map_decision(now)
        if isinstance(screen, MetroFirstAid):
            if self._heal:
                self._heal = False
                return Click("maze_first_aid_accept", "heal")
            return Click("maze_first_aid_decline", "no_heal")
        if isinstance(screen, MetroFight) and not screen.won:
            # Исход поражения живьём не видели: «Продолжить» на нём не нажимаем.
            return Halt("fight_lost")
        if screen is not None and (reason := _CONTINUE.get(type(screen))) is not None:
            return Click("maze_continue", reason)
        if isinstance(screen, MetroNpc):
            return self._npc(screen)
        if isinstance(screen, MetroChest):
            if (self.packs or 0) >= self.policy.chest_min_packs:
                return Click("maze_chest_accept", "chest")
            return Click("maze_chest_decline", "chest_few_packs")
        if isinstance(screen, MetroExit):
            self._update_mode(now)
            if self.mode == "leave":
                return Click("maze_exit_accept", self.leave_reason or "leave")
            return Click("maze_exit_decline", "explore")
        if isinstance(screen, MetroEarlyExit):
            # Необратимо: условие проверяется ещё раз по времени подтверждения.
            self._early = self._early_exit_due(now)
            if self._early:
                self.mode, self.leave_reason = "leave", "early_exit"
                return Click("maze_exit_accept", "early_exit")
            return Click("maze_exit_decline", "not_leaving")
        if isinstance(screen, MetroFinished):
            return Done("finished")
        kind = screen.kind if screen is not None else "none"
        return Halt(f"unexpected_screen:{kind}")

    def next(self, event: Event, now: datetime) -> Move:
        steps = self.steps
        self.observe(event)
        going = isinstance(event, MetroMap) and event.footer == "going"
        if self._move_sent_at is not None and not going:
            # Ход закончился новым кадром: в замер — только его время, без диалогов.
            if self.steps > steps:
                took = (now - self._move_sent_at).total_seconds()
                self.move_times = [*self.move_times, took][-RECENT_MOVES:]
            self._move_sent_at = None
        move = self.decide(now)
        if isinstance(move, Click) and move.data.removeprefix("maze_") in _MOVES:
            self._pending = move.data.removeprefix("maze_")
            self._move_sent_at = now
        return move

    def step_s(self) -> float:
        return self.budget.step_s(self.move_times)

    def _npc(self, npc: MetroNpc) -> Click:
        strength = npc.strength
        fight = self.policy.npc_low if strength == "low" else self.policy.npc_high
        if not fight:
            return Click(f"maze_npc_{strength}_decline", "npc_off")
        if not self.packs and (self.stamina or 0) < self.policy.npc_min_stamina:
            return Click(f"maze_npc_{strength}_decline", "npc_low_stamina")
        return Click(f"maze_npc_{strength}_accept", "npc")

    def _early_exit_due(self, now: datetime) -> bool:
        """Выброс игрой близко, а к обычному выходу не успеть даже при оптимистичной оценке шага
        (или выход неизвестен): только тогда — необратимый досрочный выход."""
        kick = self.budget.kick_at()
        if kick is None or now + EARLY_EXIT_LEAD < kick:
            return False
        if self.exit_at is None:
            return True
        towards, route = exit_route(self.grid, self.pos, self.exit_at)
        if towards is None:
            return True
        # Путь плюс подтверждение обычного выхода: «Выходишь?» → «Выйти».
        need = route * self.budget.optimistic_step_s(self.move_times) + CLICK_S
        return now + timedelta(seconds=need) > kick

    def _heal_fits(self, now: datetime) -> bool:
        """Перед досрочным выходом: ещё одна аптечка (два клика) и выход (два клика) успеют."""
        kick = self.budget.kick_at()
        return kick is None or now + timedelta(seconds=4 * CLICK_S) <= kick

    def _on_map_decision(self, now: datetime) -> Move:
        if self.lost:
            return Halt("lost")
        self._update_mode(now)
        # Намерение не залипает: пересчитывается по каждому кадру.
        self._early = self._early_exit_due(now)
        if self._early:
            if self.mode != "leave":
                self.mode = "leave"
                self._note_kind("leave", reason="early_exit")
            if self._needs_heal() and self._heal_fits(now):
                self._heal = True
                return Click("maze_first_aid", "heal")
            return Click("maze_exit", "early_exit")
        if self._needs_heal():
            self._heal = True
            return Click("maze_first_aid", "heal")
        if self.mode != "leave":
            direction = explore_step(self.grid, self.pos, self.exit_at, self.mode, self._last)
            if direction is not None:
                return Click(f"maze_{direction}", self.mode)
            self._leave("explored")
            if self._needs_heal():
                self._heal = True
                return Click("maze_first_aid", "heal")
        if self.exit_at is None:
            return Halt("exit_not_found")
        direction, _ = exit_route(self.grid, self.pos, self.exit_at)
        if direction is None:
            return Halt("no_route_to_exit")
        return Click(f"maze_{direction}", "leave")

    def _needs_heal(self) -> bool:
        if not self.packs or self.stamina is None or self.stamina >= FULL:
            return False
        if self.stamina <= self.policy.heal_at:
            return True
        return self.mode == "leave" and self.policy.heal_before_exit

    def _leave(self, reason: str) -> None:
        if self.mode != "leave":
            self.mode, self.leave_reason = "leave", reason
            self._note_kind("leave", reason=reason)

    def _update_mode(self, now: datetime) -> None:
        if self.mode == "leave":
            return
        if self.exit_at is not None:
            direction, route = exit_route(self.grid, self.pos, self.exit_at)
            if direction is not None and self.budget.must_leave(now, route, self.step_s()):
                self._leave("deadline")
                return
        if not targets(self.grid, reach(self.grid, self.pos)):
            self._leave("explored")
            return
        used = self.budget.used(now)
        if self.exit_at is None and used >= LATE_AT and "late_risk" not in self.alerts:
            self.alerts.append("late_risk")
            self._note_kind("late_risk", used=round(used, 3))
        self.mode = "frontier" if self.exit_at is None and used >= FRONTIER_AT else "explore"

    # --- запись и восстановление

    def snapshot(self) -> dict[str, Any]:
        return {
            "grid": self.grid.to_json(),
            "pos": list(self.pos),
            "exit": list(self.exit_at) if self.exit_at is not None else None,
            "steps": self.steps,
            "mode": self.mode,
            "leave_reason": self.leave_reason,
            "path": [list(p) for p in self.path],
            "events": self.events,
            "vitals": self.vitals,
            "alerts": self.alerts,
            "move_times": self.move_times,
            "stamina": self.stamina,
            "packs": self.packs,
        }

    @classmethod
    def restore(cls, data: dict[str, Any], policy: Policy, budget: Budget) -> MetroSolver:
        """Продолжение после рестарта: карта и позиция из сохранённого, первое окно сверяется
        с картой (`Grid.locate`)."""
        exit_at = data.get("exit")
        solver = cls(
            policy=policy,
            budget=budget,
            grid=Grid.from_json(data["grid"]),
            pos=(int(data["pos"][0]), int(data["pos"][1])),
            exit_at=(int(exit_at[0]), int(exit_at[1])) if exit_at else None,
            steps=int(data.get("steps", 0)),
            path=[(int(p[0]), int(p[1])) for p in data.get("path", [])],
            events=list(data.get("events", [])),
            vitals=list(data.get("vitals", [])),
            alerts=list(data.get("alerts", [])),
        )
        if data.get("mode") == "leave":
            solver.mode, solver.leave_reason = "leave", data.get("leave_reason")
        return solver


_MOVES = frozenset({"up", "down", "left", "right"})
