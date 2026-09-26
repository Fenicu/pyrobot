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
    prev: dict[Pos, Pos]


def reach(grid: Grid, origin: Pos) -> Reach:
    cost = {origin: 0}
    hops = {origin: 0}
    first: dict[Pos, str] = {}
    prev: dict[Pos, Pos] = {}
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
            prev[nxt] = pos
            order += 1
            heapq.heappush(queue, (price, order, nxt))
    return Reach(cost, hops, first, prev)


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
    grid: Grid,
    pos: Pos,
    exit_at: Pos | None,
    mode: Mode,
    last: str | None = None,
    keep: Pos | None = None,
) -> tuple[str, Pos] | None:
    """Следующий шаг обхода и его цель или None, если обходить больше нечего.

    Ближайшая непосещённая клетка — на дереве это обход в глубину с доведением ветки до
    конца. Если выход известен, ветка выхода откладывается на конец: сначала клетки, чей путь
    расходится с путём к выходу раньше (ответвления ближе к игроку), и только потом — ближе к
    выходу; на дереве это оптимальный обход с концом в выходе. В режиме `frontier` сначала
    клетки, шаг в которые открывает неизвестное (поиск выхода важнее полноты). Выбранная цель
    `keep` держится до прихода: пересчёт на каждом шаге мог бы качать между клетками.
    """
    r = reach(grid, pos)
    goals = targets(grid, r)
    if keep is not None and keep in goals:
        return r.first[keep], keep
    if mode == "frontier":
        informative = [p for p in goals if unknown_near(grid, p)]
        goals = informative or goals
    if not goals:
        return None
    depth = exit_depths(grid, pos, exit_at, goals) if exit_at in r.cost else {}

    def rank(p: Pos) -> tuple[int, int, int, int]:
        direction = r.first[p]
        return (
            depth.get(p, 0),
            r.cost[p],
            0 if direction == last else 1,
            _ORDER.index(direction),
        )

    best = min(goals, key=rank)
    return r.first[best], best


def exit_depths(grid: Grid, origin: Pos, exit_at: Pos, goals: list[Pos]) -> dict[Pos, int]:
    """Где цель отходит от пути игрока к выходу: число шагов от игрока до точки ответвления.

    Пути берутся по одному дереву кратчайших путей от выхода: на графе с циклами порядок
    ветвей тогда не зависит от того, откуда смотреть, и обход не качается между областями.
    """
    tree = reach(grid, exit_at)
    if origin not in tree.cost:
        return {}
    chain = [origin]
    while chain[-1] != exit_at:
        chain.append(tree.prev[chain[-1]])
    anchor = {p: i for i, p in enumerate(chain)}
    for goal in goals:
        trail = []
        node = goal
        while node not in anchor:
            trail.append(node)
            node = tree.prev[node]
        for p in trail:
            anchor[p] = anchor[node]
    return {goal: anchor[goal] for goal in goals if goal in tree.cost}


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
