from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from app.engine.parsing.metro import EXIT, FLOOR, ME, OTHER, WALL, WINDOW

Pos = tuple[int, int]
DIRS: dict[str, Pos] = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}
RADIUS = WINDOW // 2
# Переобъявить позицию можно, только если окно совпало с известной картой хотя бы на столько
# клеток: иначе совпадение случайно.
MIN_OVERLAP = 12


def step(pos: Pos, direction: str) -> Pos:
    dr, dc = DIRS[direction]
    return pos[0] + dr, pos[1] + dc


def direction_between(a: Pos, b: Pos) -> str:
    delta = (b[0] - a[0], b[1] - a[1])
    return next(d for d, v in DIRS.items() if v == delta)


def window_cells(window: Sequence[str], at: Pos) -> Iterator[tuple[Pos, str]]:
    """Клетки окна в координатах карты; игрок и незнакомые символы не отдаются."""
    for i, row in enumerate(window):
        for j, sym in enumerate(row):
            if sym in (ME, OTHER):
                continue
            yield (at[0] + i - RADIUS, at[1] + j - RADIUS), sym


@dataclass
class Grid:
    """Глобальная карта: известные клетки (стена, проход, выход) и посещённые."""

    cells: dict[Pos, str] = field(default_factory=dict)
    visited: set[Pos] = field(default_factory=set)

    def get(self, pos: Pos) -> str | None:
        return self.cells.get(pos)

    def passable(self, pos: Pos) -> bool:
        return self.cells.get(pos) in (FLOOR, EXIT)

    def neighbors(self, pos: Pos) -> Iterator[tuple[str, Pos]]:
        for direction in DIRS:
            nxt = step(pos, direction)
            if self.passable(nxt):
                yield direction, nxt

    def exits(self) -> list[Pos]:
        return sorted(p for p, sym in self.cells.items() if sym == EXIT)

    def conflicts(self, window: Sequence[str], at: Pos) -> int:
        if self.cells.get(at) == WALL:
            return WINDOW * WINDOW
        known = self.cells
        return sum(1 for p, sym in window_cells(window, at) if known.get(p, sym) != sym)

    def overlap(self, window: Sequence[str], at: Pos) -> int:
        return sum(1 for p, sym in window_cells(window, at) if self.cells.get(p) == sym)

    def merge(self, window: Sequence[str], at: Pos) -> None:
        for p, sym in window_cells(window, at):
            self.cells[p] = sym
        # Под игроком — проход (или уже известный выход).
        if self.cells.get(at) != EXIT:
            self.cells[at] = FLOOR
        self.visited.add(at)

    def locate(self, window: Sequence[str], expected: Pos, *, far: bool = False) -> Pos | None:
        """Где на известной карте снято окно: ожидаемая позиция, если окно с ней согласуется, иначе
        единственная позиция без противоречий с наибольшим совпадением (не меньше
        `MIN_OVERLAP`). При обычном ходе кандидаты — только клетки рядом с ожидаемой (ход не
        состоялся); вся карта (`far`) — только для свежего кадра после рестарта."""
        if self.conflicts(window, expected) == 0:
            return expected
        if not self.cells:
            return None
        best: list[Pos] = []
        best_overlap = MIN_OVERLAP - 1
        for at in self._candidates(expected, far):
            if self.cells.get(at) == WALL or self.conflicts(window, at):
                continue
            score = self.overlap(window, at)
            if score > best_overlap:
                best, best_overlap = [at], score
            elif score == best_overlap:
                best.append(at)
        return best[0] if len(best) == 1 else None

    def _candidates(self, expected: Pos, far: bool) -> Iterator[Pos]:
        if not far:
            yield from (step(expected, d) for d in DIRS)
            return
        rows = [p[0] for p in self.cells]
        cols = [p[1] for p in self.cells]
        for r in range(min(rows) - RADIUS, max(rows) + RADIUS + 1):
            for c in range(min(cols) - RADIUS, max(cols) + RADIUS + 1):
                yield r, c

    def to_json(self) -> dict[str, object]:
        return {
            "cells": {f"{r},{c}": sym for (r, c), sym in sorted(self.cells.items())},
            "visited": [list(p) for p in sorted(self.visited)],
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> Grid:
        raw_cells = data.get("cells") or {}
        raw_visited = data.get("visited") or []
        assert isinstance(raw_cells, dict) and isinstance(raw_visited, list)
        cells: dict[Pos, str] = {}
        for key, sym in raw_cells.items():
            r, c = (int(x) for x in str(key).split(","))
            cells[(r, c)] = str(sym)
        visited = {(int(p[0]), int(p[1])) for p in raw_visited}
        return cls(cells, visited)
