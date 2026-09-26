from datetime import timedelta

from app.engine.metro.budget import MEASURED_AFTER, Budget, prior_step_s
from app.engine.metro.grid import Grid, direction_between, step
from app.engine.metro.plan import exit_route, explore_step, reach, targets
from tests.engine.metro.helpers import T0, budget, grid_of, window_at

CORRIDOR = [
    "#######",
    "#.....#",
    "#######",
]


def test_merge_marks_cells_visited_and_keeps_exit_under_player() -> None:
    grid = Grid()
    grid.merge(window_at(CORRIDOR, (1, 1)), (1, 1))
    assert grid.visited == {(1, 1)}
    assert grid.get((1, 1)) == "." and grid.get((0, 0)) == "#" and grid.get((1, 3)) == "."
    assert grid.get((1, 4)) is None
    grid.cells[(1, 2)] = "E"
    grid.merge(window_at(CORRIDOR, (1, 2)), (1, 2))
    assert grid.get((1, 2)) == "E"


def test_repeated_corridor_windows_keep_expected_position() -> None:
    long = ["#########", "#.......#", "#########"]
    grid = Grid()
    grid.merge(window_at(long, (1, 3)), (1, 3))
    # Окна (1,3) и (1,4) одинаковы: позицию задаёт ход, а не окно.
    assert window_at(long, (1, 3)) == window_at(long, (1, 4))
    assert grid.locate(window_at(long, (1, 4)), (1, 4)) == (1, 4)


def test_contradiction_relocates_to_unique_match() -> None:
    world = [
        "#########",
        "#...#...#",
        "#.#.#.#.#",
        "#.#...#.#",
        "#########",
    ]
    grid = grid_of(world)
    wrong = (3, 7)
    assert grid.conflicts(window_at(world, (1, 1)), wrong) > 0
    # При обычном ходе дальний скачок невозможен: только по всей карте, после рестарта.
    assert grid.locate(window_at(world, (1, 1)), wrong) is None
    assert grid.locate(window_at(world, (1, 1)), wrong, far=True) == (1, 1)
    # Ход не состоялся: окно снято в соседней клетке — это позиция.
    assert grid.locate(window_at(world, (1, 3)), (1, 2)) == (1, 3)


def test_ambiguous_or_unknown_window_not_located() -> None:
    twins = ["#####", "#...#", "#####", "#####", "#...#", "#####"]
    grid = grid_of(twins)
    assert grid.locate(window_at(twins, (4, 2)), (0, 0), far=True) is None
    empty = Grid()
    assert empty.locate(window_at(twins, (1, 2)), (7, 7)) == (7, 7)


def test_grid_json_round_trip() -> None:
    grid = grid_of(["#E#", "#v.", "###"])
    again = Grid.from_json(grid.to_json())
    assert again == grid


def test_steps_and_directions() -> None:
    assert step((0, 0), "down") == (1, 0)
    assert direction_between((1, 1), (1, 0)) == "left"


BRANCHES = [
    "#########",
    "#...v...#",
    "####.####",
    "####.####",
    "#########",
]


def test_nearest_unvisited_continues_branch() -> None:
    grid = grid_of(BRANCHES)
    # Три ветки на равном расстоянии: порядок фиксирован (вверх, вниз, влево, вправо).
    assert explore_step(grid, (1, 4), None, "explore") == ("down", (2, 4))
    grid.visited.add((1, 3))
    assert explore_step(grid, (1, 3), None, "explore", "left") == ("left", (1, 2))


def test_exit_branch_deferred() -> None:
    with_exit = [row.replace("...#", "..E#") if i == 1 else row for i, row in enumerate(BRANCHES)]
    grid = grid_of(with_exit)
    # Налево и направо — по две клетки, но справа выход: сначала налево и вниз.
    first = explore_step(grid, (1, 4), (1, 7), "explore")
    assert first is not None and first[0] in ("left", "down")
    grid.visited |= {(1, 1), (1, 2), (1, 3), (2, 4), (3, 4)}
    assert explore_step(grid, (1, 4), (1, 7), "explore") == ("right", (1, 5))


def test_frontier_prefers_cells_that_reveal_unknown() -> None:
    rows = [
        "##########",
        "##########",
        "##.v......",
        "##########",
        "##########",
    ]
    grid = grid_of(rows)
    # Слева тупик, известный целиком, справа коридор уходит в неизвестное.
    assert explore_step(grid, (2, 3), None, "explore") == ("left", (2, 2))
    assert explore_step(grid, (2, 3), None, "frontier") == ("right", (2, 8))


def test_route_avoids_passing_through_exit() -> None:
    rows = [
        "######",
        "#.vE.#",
        "#v...#",
        "######",
    ]
    grid = grid_of(rows)
    r = reach(grid, (1, 2))
    assert r.first[(1, 4)] == "down"
    assert (1, 3) not in targets(grid, r)


def test_exit_route_from_exit_steps_off_and_back() -> None:
    grid = grid_of(["###", "#E#", "#v#", "###"])
    assert exit_route(grid, (1, 1), (1, 1)) == ("down", 2)
    assert exit_route(grid, (2, 1), (1, 1)) == ("up", 1)


def test_budget_phases() -> None:
    b = budget(minutes=125, margin_min=25)
    assert b.total_s() == 100 * 60
    assert b.used(T0 + timedelta(minutes=60)) == 0.6
    # Время шага — медиана недавних ходов; пока их мало — не быстрее априорного.
    assert b.step_s([]) == 5.0
    assert b.step_s([3.0] * 5) == 5.0
    assert b.step_s([4.0] * MEASURED_AFTER) == 4.0
    assert b.step_s([100.0] * 30 + [4.0] * 20) == 4.0
    # Для необратимых решений — оптимистично: меньшее из замера и априорного.
    assert b.optimistic_step_s([]) == 5.0
    assert b.optimistic_step_s([30.0] * 20) == 5.0
    assert b.optimistic_step_s([3.0] * 3) == 3.0
    # Путь 60 шагов по 5 с × 1.5 = 7.5 мин + 25 мин запаса < 35 мин до битвы; по 10 с — нет.
    fixed = Budget(T0, T0 + timedelta(minutes=40), timedelta(minutes=25), 5.0)
    assert fixed.must_leave(T0 + timedelta(minutes=5), 60, 5.0) is False
    assert fixed.must_leave(T0 + timedelta(minutes=5), 60, 10.0) is True
    assert budget(minutes=None).must_leave(T0, 1000, 5.0) is False
    # Игра выкидывает за 15 минут до битвы.
    assert b.kick_at() == T0 + timedelta(minutes=110)
    assert budget(minutes=None).kick_at() is None
    assert prior_step_s(True) < prior_step_s(False)


def test_chosen_target_kept_until_reached() -> None:
    grid = grid_of(BRANCHES)
    # Вниз ближе, но уже выбранная цель слева держится.
    assert explore_step(grid, (1, 4), None, "explore", keep=(1, 1)) == ("left", (1, 1))
    grid.visited.add((1, 1))
    assert explore_step(grid, (1, 4), None, "explore", keep=(1, 1)) == ("down", (2, 4))
