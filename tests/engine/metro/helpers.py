from datetime import UTC, datetime, timedelta

from app.engine.metro.budget import Budget
from app.engine.metro.grid import Grid, Pos
from app.engine.parsing.metro import MetroMap

T0 = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


def grid_of(rows: list[str], visited: str = "v") -> Grid:
    """Карта из строк: `#` стена, `.` проход, `v` посещённый проход, `E` выход, пробел —
    неизвестно. Координаты — (строка, столбец) от левого верхнего угла."""
    grid = Grid()
    for r, row in enumerate(rows):
        for c, sym in enumerate(row):
            if sym == " ":
                continue
            grid.cells[(r, c)] = "." if sym == visited else sym
            if sym == visited:
                grid.visited.add((r, c))
    return grid


def window_at(rows: list[str], at: Pos) -> tuple[str, ...]:
    """Окно 5×5 вокруг `at` из полной карты (за краем — стена), игрок в центре."""
    out = []
    for dr in range(-2, 3):
        line = ""
        for dc in range(-2, 3):
            r, c = at[0] + dr, at[1] + dc
            sym = rows[r][c] if 0 <= r < len(rows) and 0 <= c < len(rows[r]) else "#"
            line += "@" if (dr, dc) == (0, 0) else ("." if sym == "v" else sym)
        out.append(line)
    return tuple(out)


def map_frame(
    window: tuple[str, ...],
    footer: str = "arrived",
    direction: str | None = None,
    stamina: int = 100,
    packs: int | None = 7,
) -> MetroMap:
    return MetroMap(
        stamina=stamina,
        window=window,
        footer=footer,  # type: ignore[arg-type]
        direction=direction,
        packs=packs,
    )


def budget(minutes: float | None = 120, margin_min: float = 25, step_s: float = 5.0) -> Budget:
    battle = T0 + timedelta(minutes=minutes) if minutes is not None else None
    return Budget(T0, battle, timedelta(minutes=margin_min), step_s)
