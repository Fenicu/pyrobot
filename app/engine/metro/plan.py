from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Literal

from app.engine.metro.grid import DIRS, RADIUS, Grid, Pos
from app.engine.parsing.metro import EXIT, FLOOR

Mode = Literal["explore", "frontier", "leave"]
# Лишний шаг через клетку выхода стоит ещё и диалога «Выходишь?» → «Остаться».
EXIT_DETOUR = 3
_ORDER = tuple(DIRS)


@dataclass(frozen=True, slots=True)
class Reach:
    """Кратчайшие маршруты от точки по известным проходам: цена, число шагов, первый шаг."""

    cost: dict[Pos, int]
    hops: dict[Pos, int]
    first: dict[Pos, str]


def reach(grid: Grid, origin: Pos) -> Reach:
    cost = {origin: 0}
    hops = {origin: 0}
    first: dict[Pos, str] = {}
    queue: list[tuple[int, int, Pos]] = [(0, 0, origin)]
    order = 0
    while queue:
        spent, _, pos = heapq.heappop(queue)
        if spent > cost[pos]:
            continue
        for direction, nxt in grid.neighbors(pos):
            price = spent + 1 + (EXIT_DETOUR if grid.get(nxt) == EXIT else 0)
            if price >= cost.get(nxt, price + 1):
                continue
            cost[nxt] = price
            hops[nxt] = hops[pos] + 1
            first[nxt] = first.get(pos, direction)
            order += 1
            heapq.heappush(queue, (price, order, nxt))
    return Reach(cost, hops, first)


def unknown_near(grid: Grid, pos: Pos) -> bool:
    """Шаг в клетку откроет что-то новое: в её окне есть неизвестные клетки."""
    return any(
        (pos[0] + dr, pos[1] + dc) not in grid.cells
        for dr in range(-RADIUS, RADIUS + 1)
        for dc in range(-RADIUS, RADIUS + 1)
    )


def targets(grid: Grid, r: Reach) -> list[Pos]:
    """Непосещённые достижимые проходы; выход — не цель обхода, в него идут последним."""
    return [p for p in r.cost if p not in grid.visited and grid.get(p) == FLOOR]


def explore_step(
    grid: Grid, pos: Pos, exit_at: Pos | None, mode: Mode, last: str | None = None
) -> str | None:
    """Следующий шаг обхода или None, если обходить больше нечего.

    Ближайшая непосещённая клетка — на дереве это обход в глубину с доведением ветки до
    конца. Если выход известен, ветки в его сторону откладываются на конец, а при равных
    расстояниях первой берётся клетка дальше от выхода. В режиме `frontier` сначала клетки,
    шаг в которые открывает неизвестное (поиск выхода важнее полноты).
    """
    r = reach(grid, pos)
    goals = targets(grid, r)
    if mode == "frontier":
        informative = [p for p in goals if unknown_near(grid, p)]
        goals = informative or goals
    far: dict[Pos, int] = {}
    if exit_at is not None and exit_at in r.cost:
        towards = r.first.get(exit_at)
        away = [p for p in goals if r.first[p] != towards]
        goals = away or goals
        far = reach(grid, exit_at).hops
    if not goals:
        return None

    def rank(p: Pos) -> tuple[int, int, int, int]:
        direction = r.first[p]
        return (
            r.cost[p],
            -far.get(p, 0),
            0 if direction == last else 1,
            _ORDER.index(direction),
        )

    return r.first[min(goals, key=rank)]


def exit_route(grid: Grid, pos: Pos, exit_at: Pos) -> tuple[str | None, int]:
    """Первый шаг к выходу и длина пути в шагах; стоя на выходе — сойти и вернуться."""
    if pos == exit_at:
        for direction, _ in grid.neighbors(pos):
            return direction, 2
        return None, 0
    r = reach(grid, pos)
    if exit_at not in r.cost:
        return None, 0
    return r.first[exit_at], r.hops[exit_at]


def reachable_exit(grid: Grid, pos: Pos) -> Pos | None:
    r = reach(grid, pos)
    known = [p for p in grid.exits() if p in r.cost]
    return min(known, key=lambda p: r.cost[p]) if known else None
