import json
import random
from datetime import timedelta
from pathlib import Path

import pytest

from app.engine.metro.solver import Done, Halt, Policy
from app.engine.parsing.metro import MetroLoot, MetroMap, recognize_metro
from tests.engine.metro.helpers import T0
from tests.engine.metro.sim import (
    Drive,
    Maze,
    corridor_maze,
    drive,
    hide_events,
    loopy_maze,
    render,
    text_message,
    tree_maze,
)
from tests.fixtures import game_versions

RUN1 = Path(__file__).parents[2] / "fixtures" / "metro" / "run1.json"
SIZES = [(4, 4), (6, 5), (8, 6), (10, 10)]
SEEDS = range(6)


@pytest.mark.parametrize(("run", "frames"), [(3624441, 528), (3625352, 394)])
def test_simulator_frames_match_real_game_text(run: int, frames: int) -> None:
    """Круговой тест: кадр игры → событие → кадр симулятора совпадает до символа и кнопки
    (оба живых забега без экранов входа и бафов)."""
    checked = 0
    for msg in game_versions("metro", run)[5:]:
        [event] = recognize_metro(msg)
        dot = isinstance(event, MetroMap) and (msg.text or "").endswith(".")
        text, buttons = render(event, dot=dot)
        assert text == msg.text, event
        assert [(b.text, b.data, b.row, b.col) for b in buttons] == [
            (b.text, b.data, b.row, b.col) for b in msg.inline
        ], event
        checked += 1
    assert checked == frames


def _complete(maze: Maze, run: Drive) -> bool:
    floor = {p for p, sym in maze.cells.items() if sym == "."}
    return floor <= run.solver.grid.visited


def _maze(kind: str, rows: int, cols: int, seed: int) -> Maze:
    rng = random.Random(seed * 1000 + rows * 10 + cols)
    maze = tree_maze(rows, cols, rng) if kind == "tree" else loopy_maze(rows, cols, rng)
    return hide_events(maze, rng)


@pytest.mark.parametrize(("rows", "cols"), SIZES)
def test_trees_fully_explored_within_dfs_bound(rows: int, cols: int) -> None:
    ratios = []
    for seed in SEEDS:
        maze = _maze("tree", rows, cols, seed)
        run = drive(maze)
        n = len(maze.floor())
        assert maze.edges() == n - 1
        assert run.outcome == Done("finished") and _complete(maze, run)
        assert run.game.wall_hits == 0
        # Обход в глубину с концом в выходе: не больше 2(n−1) + путь от входа до выхода.
        assert run.solver.steps <= 2 * (n - 1) + maze.distances(maze.start)[maze.exit]
        ratios.append(run.solver.steps / (2 * (n - 1)))
    assert sum(ratios) / len(ratios) <= 1.0


@pytest.mark.parametrize(("rows", "cols"), SIZES)
def test_mazes_with_cycles_fully_explored(rows: int, cols: int) -> None:
    for seed in SEEDS:
        maze = _maze("loopy", rows, cols, seed)
        run = drive(maze)
        n = len(maze.floor())
        assert maze.edges() > n - 1
        assert run.outcome == Done("finished") and _complete(maze, run)
        assert run.solver.steps / n <= 2.5


def test_repeated_windows_of_straight_corridor() -> None:
    maze = corridor_maze(30)
    run = drive(maze)
    assert run.outcome == Done("finished") and _complete(maze, run)
    # Сначала дальний от выхода конец, потом весь коридор к выходу.
    assert run.solver.steps == 14 + 29


def test_contradicting_windows_relocate_or_pause() -> None:
    """Игра «не сдвинула» игрока, но показала подпись прихода: окно противоречит карте."""
    outcomes = []
    for seed in range(12):
        maze = _maze("tree", 7, 7, seed)
        # Симулятор падает на ходе в стену: решатель с неверной позицией бы в неё пошёл.
        run = drive(maze, glitch_moves=frozenset({5, 40, 77}))
        kinds = [e["kind"] for e in run.solver.events]
        assert "relocated" in kinds or "lost" in kinds
        if run.outcome == Halt("lost"):
            outcomes.append("lost")
            continue
        assert run.outcome == Done("finished") and _complete(maze, run)
        outcomes.append("done")
    assert outcomes.count("done") >= 10


def test_budget_leaves_before_battle() -> None:
    battle = timedelta(minutes=40)
    reasons = set()
    for seed in SEEDS:
        maze = _maze("tree", 10, 10, seed)
        run = drive(maze, battle_in=battle)
        assert run.outcome == Done("finished")
        reasons.add(run.solver.leave_reason)
        # Игра выкидывает за 15 минут до битвы с половиной найденного.
        assert run.finished_at <= T0 + battle - timedelta(minutes=15)
        assert run.game.early_exit == (run.solver.leave_reason == "early_exit")
    assert "deadline" in reasons


def test_early_exit_when_exit_not_found_in_time() -> None:
    # Большой лабиринт и 20 минут до битвы: выход не найден — сами жмём 🚪 до выброса.
    battle = timedelta(minutes=20)
    maze = _maze("tree", 14, 14, 3)
    run = drive(maze, battle_in=battle)
    assert run.outcome == Done("finished")
    assert (run.solver.leave_reason, run.solver.exit_at) == ("early_exit", None)
    assert run.game.early_exit and run.game.wall_hits == 0
    assert run.solver.result == {k: v - v // 2 for k, v in run.game.bank.items()}
    kick = T0 + battle - timedelta(minutes=15)
    assert kick - timedelta(minutes=1) <= run.finished_at <= kick


def test_no_fight_without_packs_and_stamina() -> None:
    maze = _maze("tree", 6, 5, 2)
    run = drive(maze, stamina=20, packs=0)
    kinds = [e["kind"] for e in run.solver.events]
    assert "metro_npc" in kinds and "metro_fight" not in kinds
    assert run.outcome == Done("finished") and _complete(maze, run)


def test_heal_rules_hold_on_every_move() -> None:
    for seed in SEEDS:
        maze = _maze("tree", 8, 6, seed)
        run = drive(maze, npc_costs=(45, 30, 60))
        assert run.heal_misses == 0
        assert run.outcome == Done("finished")
        assert run.game.stamina == 100 or run.game.packs == 0


def test_chests_skipped_when_few_packs() -> None:
    maze = _maze("tree", 6, 5, 1)
    run = drive(maze, packs=1)
    opened = [e for e in run.solver.events if e["kind"] == "metro_chest_opened"]
    assert not opened and any(e["kind"] == "metro_chest" for e in run.solver.events)


def test_strong_npc_policy() -> None:
    maze = _maze("tree", 6, 5, 2)
    maze.npcs = dict.fromkeys(maze.npcs, "high")
    run = drive(maze)
    assert not [e for e in run.solver.events if e["kind"] == "metro_fight"]
    fought = drive(_with_strong(_maze("tree", 6, 5, 2)), policy=Policy(npc_high=True))
    assert [e for e in fought.solver.events if e["kind"] == "metro_fight"]


def _with_strong(maze: Maze) -> Maze:
    maze.npcs = dict.fromkeys(maze.npcs, "high")
    return maze


def _run1() -> Maze:
    run = json.loads(RUN1.read_text())
    symbols = {"⬛": "#", "⬜": ".", "🚪": "E"}
    cells = {tuple(map(int, k.split(","))): symbols[v] for k, v in run["cells"].items()}
    maze = Maze(cells, (0, 0))  # type: ignore[arg-type]
    for event in run["events"]:
        pos, text = tuple(event["pos"]), event["text"]
        if "Нашёл" in text:
            [loot] = recognize_metro(text_message(text))
            assert isinstance(loot, MetroLoot)
            maze.loot[pos] = (loot.item, loot.amount)
        elif "сразился" in text or "Продавана" in text:
            maze.npcs[pos] = "low"
        elif "стрела" in text:
            maze.chests[pos] = "arrow"
        elif "тайник" in text:
            maze.chests[pos] = "stash"
        elif "граната" in text:
            maze.chests[pos] = "grenade"
    return maze


def test_recorded_run_map_replayed_by_solver() -> None:
    maze = _run1()
    assert len(maze.floor()) == 95 and maze.edges() == 94
    run = drive(maze)
    assert run.outcome == Done("finished") and _complete(maze, run)
    # Живой забег (жадный BFS) — 246 шагов; обход в глубину с веткой выхода в конце — 162.
    assert run.solver.steps <= 170
    assert run.game.moves == run.solver.steps
    assert len(maze.loot) == 14 and len(maze.chests) == 3


def test_unknown_exit_halts_nowhere_to_go() -> None:
    maze = corridor_maze(6)
    maze.cells[(1, 6)] = "."
    run = drive(maze)
    assert run.outcome == Halt("exit_not_found")
