import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.engine.events import Event
from app.engine.metro.budget import Budget
from app.engine.metro.solver import Click, Done, Halt, MetroSolver, Policy, policy_of
from app.engine.parsing.metro import (
    MetroBuffs,
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
    recognize_metro,
)
from app.engine.settings import MetroSection
from tests.engine.metro.helpers import (
    POLICY,
    T0,
    budget,
    grid_of,
    map_frame,
    policy_with,
    window_at,
)
from tests.fixtures import game_versions

RUN1 = Path(__file__).parents[2] / "fixtures" / "metro" / "run1.json"
RUN2 = Path(__file__).parents[2] / "fixtures" / "metro" / "run2.json"
SYMBOLS = {"⬛": "#", "⬜": ".", "🚪": "E"}

# Дерево: вход (1,1), тупик вправо, ветка вниз ведёт к выходу (4,5).
TREE = [
    "#######",
    "#.....#",
    "#.###.#",
    "#.#####",
    "#.#..E#",
    "#...###",
    "#######",
]


def solver(**policy: object) -> MetroSolver:
    return MetroSolver(policy_with(**policy), budget(), pos=(1, 1))


def at(pos: tuple[int, int], footer: str = "entry", **kw: object) -> MetroMap:
    direction = kw.pop("direction", None)
    return map_frame(window_at(TREE, pos), footer, direction, **kw)  # type: ignore[arg-type]


def test_replay_of_recorded_run_rebuilds_its_map_and_path() -> None:
    run = json.loads(RUN1.read_text())
    s = MetroSolver(POLICY, budget())
    started = False
    for msg in game_versions("metro", 3624441):
        for event in recognize_metro(msg):
            started = started or (isinstance(event, MetroMap) and event.footer == "entry")
            if started and not isinstance(event, MetroBuffs):
                s.observe(event)
    assert s.path == [tuple(p) for p in run["path"]]
    assert s.steps == 246
    expected = {tuple(map(int, k.split(","))): SYMBOLS[v] for k, v in run["cells"].items()}
    assert s.grid.cells == expected
    assert s.grid.visited == {tuple(map(int, k.split(","))) for k in run["visited"]}
    assert s.exit_at == tuple(run["exit"])
    found = {tuple(e["pos"]) for e in s.events if e["kind"] not in ("metro_npc", "metro_chest")}
    recorded = {tuple(e["pos"]) for e in run["events"]}
    assert recorded <= found
    assert s.result is not None and s.result["money"] == 157


def test_first_frame_explores_nearest_branch() -> None:
    s = solver()
    move = s.next(at((1, 1)), T0)
    assert move == Click("maze_down", "explore")
    assert s.grid.visited == {(1, 1)}


def test_arrival_moves_position_and_repeats_until_branch_end() -> None:
    s = solver()
    s.next(at((1, 1)), T0)
    move = s.next(at((2, 1), "arrived", direction="down"), T0)
    assert s.pos == (2, 1) and s.steps == 1
    assert move == Click("maze_down", "explore")


@pytest.mark.parametrize(
    ("screen", "packs", "expected"),
    [
        (MetroLoot(item="money", amount=68), 7, "maze_continue"),
        (MetroFight(enemy="👨", won=True, stamina=55), 7, "maze_continue"),
        (MetroChestOpened(result="grenade"), 7, "maze_continue"),
        (MetroNpc(strength="low"), 7, "maze_npc_low_accept"),
        (MetroNpc(strength="high"), 7, "maze_npc_high_decline"),
        (MetroChest(), 2, "maze_chest_accept"),
        (MetroChest(), 1, "maze_chest_decline"),
        (MetroFirstAid(packs=7, stamina=44, after=94), 7, "maze_first_aid_decline"),
    ],
)
def test_event_policies(screen: Event, packs: int, expected: str) -> None:
    s = solver()
    s.next(at((1, 1), packs=packs), T0)
    move = s.next(screen, T0)
    assert isinstance(move, Click) and move.data == expected


def test_strong_npc_fought_when_enabled() -> None:
    s = solver(npc_high=True)
    s.next(at((1, 1)), T0)
    assert s.next(MetroNpc(strength="high"), T0) == Click("maze_npc_high_accept", "npc")


def test_event_screen_places_player_on_the_new_cell() -> None:
    s = solver()
    s.next(at((1, 1)), T0)
    s.next(MetroLoot(item="burger", amount=2), T0)
    assert s.pos == (2, 1) and (2, 1) in s.grid.visited
    assert s.events[-1] == {
        "step": 1,
        "pos": [2, 1],
        "kind": "metro_loot",
        "item": "burger",
        "amount": 2,
    }
    s.next(at((2, 1), "waiting"), T0)
    assert s.pos == (2, 1)


def test_heal_at_threshold_then_accept() -> None:
    s = solver()
    assert s.next(at((1, 1), stamina=50), T0) == Click("maze_first_aid", "heal")
    assert s.next(MetroFirstAid(packs=7, stamina=50, after=100), T0) == Click(
        "maze_first_aid_accept", "heal"
    )
    move = s.next(at((1, 1), "none", stamina=100, packs=6), T0)
    assert move == Click("maze_down", "explore") and s.packs == 6


def test_no_heal_without_packs_or_above_threshold() -> None:
    assert solver().next(at((1, 1), stamina=51), T0) == Click("maze_down", "explore")
    assert solver().next(at((1, 1), stamina=0, packs=0), T0) == Click("maze_down", "explore")


def test_arrow_trap_zeroes_stamina_and_next_map_heals() -> None:
    s = solver()
    s.next(at((1, 1)), T0)
    s.next(MetroChest(), T0)
    s.next(MetroChestOpened(result="arrow"), T0)
    assert s.stamina == 0
    assert s.next(at((2, 1), "waiting", stamina=0), T0) == Click("maze_first_aid", "heal")


def _explored(s: MetroSolver) -> None:
    """Всё, кроме выхода, посещено; игрок у выхода слева."""
    s.grid = grid_of(TREE)
    s.grid.visited = {p for p, sym in s.grid.cells.items() if sym == "."}
    s.pos, s.exit_at = (4, 4), (4, 5)


def test_explored_heals_to_full_then_goes_to_exit_and_accepts() -> None:
    s = solver()
    _explored(s)
    assert s.next(at((4, 4), "waiting", stamina=60, packs=2), T0) == Click(
        "maze_first_aid", "heal"
    )
    assert (s.mode, s.leave_reason) == ("leave", "explored")
    s.next(MetroFirstAid(packs=2, stamina=60, after=100), T0)
    assert s.next(at((4, 4), "none", stamina=100, packs=1), T0) == Click("maze_right", "leave")
    assert s.next(MetroExit(found={"money": 1}), T0) == Click("maze_exit_accept", "explored")
    assert s.next(MetroFinished(loot={"money": 1}, stamina=100), T0) == Done("finished")
    assert s.result == {"money": 1}


def test_exit_declined_while_cells_remain() -> None:
    s = solver()
    s.grid = grid_of(TREE)
    s.grid.visited = {(4, 4)}
    s.pos = (4, 4)
    s.observe(at((4, 4), "going", direction="right"))
    assert s.next(MetroExit(), T0) == Click("maze_exit_decline", "explore")
    assert s.exit_at == (4, 5)


def test_deadline_sends_to_known_exit() -> None:
    s = MetroSolver(POLICY, budget(minutes=40, margin_min=25), pos=(1, 1))
    s.next(at((1, 1)), T0)
    s.exit_at = (4, 5)
    s.grid = grid_of(TREE)
    s.grid.visited = {(1, 1)}
    late = T0 + timedelta(minutes=14)
    move = s.next(at((1, 1), "waiting", stamina=100), late)
    assert (s.mode, s.leave_reason) == ("leave", "deadline")
    assert move == Click("maze_down", "leave")


def test_unknown_exit_frontier_and_late_risk() -> None:
    s = MetroSolver(POLICY, budget(minutes=125, margin_min=25), pos=(1, 1))
    s.next(at((1, 1)), T0)
    s.next(at((1, 1), "waiting"), T0 + timedelta(minutes=61))
    assert s.mode == "frontier" and s.alerts == []
    s.next(at((1, 1), "waiting"), T0 + timedelta(minutes=86))
    assert s.alerts == ["late_risk"]
    s.next(at((1, 1), "waiting"), T0 + timedelta(minutes=90))
    assert s.alerts == ["late_risk"]


def test_explored_without_exit_halts() -> None:
    s = solver()
    s.grid = grid_of(["###", "#v#", "###"])
    s.pos = (1, 1)
    assert s.next(map_frame(("#####", "#####", "##@##", "#####", "#####"), "waiting"), T0) == Halt(
        "exit_not_found"
    )


def _known_tree(pos: tuple[int, int]) -> MetroSolver:
    s = solver()
    s.grid = grid_of(TREE)
    s.grid.visited = {(1, 1)}
    s.pos = pos
    return s


def test_contradicting_window_relocates_to_neighbour_only() -> None:
    # Ход вниз из (1,1) «пришёл», но окно — всё ещё (1,1): ход не состоялся.
    s = _known_tree((1, 1))
    s.observe(at((1, 1), "going", direction="down"))
    move = s.next(at((1, 1), "arrived", direction="down"), T0)
    assert s.pos == (1, 1) and s.events[-1]["kind"] == "relocated"
    assert isinstance(move, Click)


def test_far_window_on_normal_move_halts() -> None:
    # Окно из далёкого уже известного участка после обычного хода — не скачок, а остановка.
    s = _known_tree((1, 1))
    assert s.next(at((5, 3), "waiting"), T0) == Halt("lost")


def test_far_window_after_restart_relocates() -> None:
    s = _known_tree((1, 1))
    s.resync()
    assert isinstance(s.next(at((5, 3), "waiting"), T0), Click)
    assert s.pos == (5, 3) and s.events[-1]["kind"] == "relocated"
    lost = map_frame(("#####", "#.#.#", "#.@.#", "#.#.#", "#####"), "waiting")
    assert s.next(lost, T0) == Halt("lost")


def test_unexpected_screen_halts() -> None:
    s = solver()
    s.next(at((1, 1)), T0)
    buffs = MetroBuffs(bought=(), offers=(), tokens=0, coins=0)
    assert s.next(buffs, T0) == Halt("unexpected_screen:metro_buffs")


def test_snapshot_is_json() -> None:
    s = solver()
    s.next(at((1, 1)), T0)
    s.next(at((2, 1), "arrived", direction="down"), T0)
    snap = json.loads(json.dumps(s.snapshot()))
    assert snap["pos"] == [2, 1] and snap["steps"] == 1 and snap["path"] == [[1, 1], [2, 1]]
    assert snap["grid"]["visited"] == [[1, 1], [2, 1]]


def test_replay_of_second_run_matches_its_snapshot() -> None:
    """Второй живой забег (решатель прототипа): отказы от NPC и сундука, стена, досрочный
    выход с отказом — те же 182 шага, 95 клеток, выход (14, −2) и итог, что и в снимке."""
    run = json.loads(RUN2.read_text())
    s = MetroSolver(POLICY, budget())
    started = False
    for msg in game_versions("metro", 3625352):
        for event in recognize_metro(msg):
            started = started or (isinstance(event, MetroMap) and event.footer == "entry")
            if started and not isinstance(event, MetroBuffs):
                s.observe(event)
    assert (s.steps, len(s.grid.visited), s.exit_at) == (182, 95, (14, -2))
    assert s.grid.to_json() == run["grid"]
    assert [list(p) for p in s.path] == run["path"]
    assert s.result == run["result"]
    kinds = [e["kind"] for e in s.events]
    assert "wall" in kinds and "metro_early_exit" in kinds


@pytest.mark.parametrize("footer", ["npc_declined", "chest_declined", "wall", "stayed"])
def test_staying_footers_do_not_move(footer: str) -> None:
    s = solver()
    s.next(at((1, 1)), T0)
    s.observe(at((1, 1), "going", direction="down"))
    s.next(at((1, 1), footer), T0)
    assert (s.pos, s.steps) == ((1, 1), 0)


def test_npc_policy_without_packs() -> None:
    low = solver()
    low.next(at((1, 1), stamina=29, packs=0), T0)
    assert low.next(MetroNpc(strength="low"), T0) == Click(
        "maze_npc_low_decline", "npc_low_stamina"
    )
    healable = solver()
    healable.next(at((1, 1), stamina=10, packs=1), T0)
    assert healable.next(MetroNpc(strength="low"), T0) == Click("maze_npc_low_accept", "npc")
    enough = solver()
    enough.next(at((1, 1), stamina=30, packs=0), T0)
    assert enough.next(MetroNpc(strength="low"), T0) == Click("maze_npc_low_accept", "npc")
    off = solver(npc_low=False)
    off.next(at((1, 1)), T0)
    assert off.next(MetroNpc(strength="low"), T0) == Click("maze_npc_low_decline", "npc_off")


def test_lost_fight_halts_without_continue() -> None:
    s = solver()
    s.next(at((1, 1)), T0)
    s.next(MetroNpc(strength="low"), T0)
    assert s.next(MetroFight(enemy="👨", won=False, stamina=0), T0) == Halt("fight_lost")


def _near_kick(minutes_to_battle: float) -> tuple[MetroSolver, datetime]:
    s = MetroSolver(POLICY, budget(minutes=40, margin_min=25), pos=(1, 1))
    s.next(at((1, 1)), T0)
    return s, T0 + timedelta(minutes=40 - minutes_to_battle)


def test_early_exit_when_exit_unknown_near_kick() -> None:
    s, now = _near_kick(16.5)
    # До выброса (битва − 15) больше минуты — ещё обход.
    assert s.next(at((1, 1), "waiting"), now).data != "maze_exit"  # type: ignore[union-attr]
    s, now = _near_kick(15.9)
    assert s.next(at((1, 1), "waiting", stamina=60, packs=1), now) == Click(
        "maze_first_aid", "heal"
    )
    s.next(MetroFirstAid(packs=1, stamina=60, after=100), now)
    assert s.next(at((1, 1), "none", stamina=100, packs=0), now) == Click(
        "maze_exit", "early_exit"
    )
    offer = MetroEarlyExit(found={"money": 3}, half={"money": 2})
    assert s.next(offer, now) == Click("maze_exit_accept", "early_exit")
    assert (s.mode, s.leave_reason) == ("leave", "early_exit")


def test_early_exit_when_known_exit_too_far() -> None:
    s, now = _near_kick(15.5)
    s.exit_at = (4, 5)
    s.grid = grid_of(TREE)
    s.grid.visited = {(1, 1)}
    # До выхода 9 шагов по 5 с и подтверждение — 47 с, а до выброса 30 с.
    assert s.next(at((1, 1), "waiting"), now) == Click("maze_exit", "early_exit")
    reachable, soon = _near_kick(15.9)
    reachable.exit_at = (4, 5)
    reachable.grid = grid_of(TREE)
    reachable.grid.visited = {(1, 1)}
    assert reachable.next(at((1, 1), "waiting"), soon) == Click("maze_down", "leave")


def test_unrequested_early_exit_offer_declined() -> None:
    s = solver()
    s.next(at((1, 1)), T0)
    assert s.next(MetroEarlyExit(found={}, half={}), T0) == Click(
        "maze_exit_decline", "not_leaving"
    )


# Коридор: вход слева, выход в конце справа.
CORRIDOR = ["#" * 105, "#" * 105, "#" + "." * 101 + "E##", "#" * 105, "#" * 105]


def test_downtime_does_not_inflate_step_time() -> None:
    """100 шагов за 10 минут, затем час простоя; до выброса 30 с, выход в одном шаге —
    обычный выход, а не 🚪: время шага меряется по ходам, простой в него не входит."""
    kick = T0 + timedelta(minutes=10, hours=1, seconds=30)
    b = Budget(T0, kick + timedelta(minutes=15), timedelta(minutes=25), 5.0)
    s = MetroSolver(POLICY, b, pos=(2, 1))
    now = T0
    s.next(map_frame(window_at(CORRIDOR, (2, 1)), "entry"), now)
    for col in range(2, 102):
        now += timedelta(seconds=6)
        s.next(map_frame(window_at(CORRIDOR, (2, col)), "arrived", "right"), now)
    assert s.steps == 100 and s.pos == (2, 101) and s.step_s() == 6.0
    later = kick - timedelta(seconds=30)
    move = s.next(map_frame(window_at(CORRIDOR, (2, 101)), "waiting"), later)
    assert move == Click("maze_right", "leave")


def test_early_intent_dropped_when_exit_comes_close() -> None:
    # Выход за краем окна — за минуту до выброса 🚪; ручной ход приблизил выход — выход обычный.
    short = ["#" * 103, "#" * 103, "#" + "." * 99 + "E##", "#" * 103, "#" * 103]
    known = [row[:100] for row in short]
    known[2] = "#" + "v" * 97 + ".."
    kick = T0 + timedelta(minutes=10)
    b = Budget(T0, kick + timedelta(minutes=15), timedelta(minutes=25), 5.0)
    s = MetroSolver(POLICY, b, pos=(2, 97))
    s.grid = grid_of(known)
    now = kick - timedelta(seconds=40)
    assert s.next(map_frame(window_at(short, (2, 97)), "waiting"), now) == Click(
        "maze_exit", "early_exit"
    )
    s.cancel()
    moved = s.next(map_frame(window_at(short, (2, 98)), "arrived", "right"), now)
    assert s.exit_at == (2, 100) and moved == Click("maze_right", "leave")
    # Диалог досрочного выхода, когда он уже не нужен, — «Остаться».
    offer = MetroEarlyExit(found={}, half={})
    assert s.next(offer, now) == Click("maze_exit_decline", "not_leaving")


def test_no_heal_when_it_would_miss_the_kick() -> None:
    kick = T0 + timedelta(minutes=10)
    b = Budget(T0, kick + timedelta(minutes=15), timedelta(minutes=25), 5.0)
    s = MetroSolver(POLICY, b, pos=(1, 1))
    frame = at((1, 1), "waiting", stamina=60, packs=3)
    assert s.next(frame, kick - timedelta(seconds=30)) == Click("maze_first_aid", "heal")
    rushed = MetroSolver(POLICY, b, pos=(1, 1))
    assert rushed.next(frame, kick - timedelta(seconds=5)) == Click("maze_exit", "early_exit")


def test_exit_screen_without_awaited_move_steps_onto_the_known_exit() -> None:
    """После рестарта текущий экран — «Выходишь?», а «Идёшь …» до журнала не дошёл: выход —
    единственная известная соседняя клетка, персонаж на ней; второго выхода нет, «Остался» —
    там же."""
    events = [recognize_metro(m)[0] for m in game_versions("metro", 3624441)]
    s = MetroSolver(POLICY, budget())
    for event in events[5:278]:
        s.observe(event)
    exit_at = s.exit_at
    assert exit_at is not None and s.grid.exits() == [exit_at]
    s.resync()
    assert s.next(events[279], T0) == Click("maze_exit_decline", "explore")
    assert (s.pos, s.exit_at, s.grid.exits()) == (exit_at, exit_at, [exit_at])
    assert isinstance(s.next(events[280], T0), Click) and not s.lost


def test_exit_screen_without_awaited_move_far_from_exits_waits_for_map() -> None:
    # Рядом с позицией выхода нет: клетка и выход не трогаются, позицию найдёт следующий кадр.
    s = _known_tree((4, 3))
    s.exit_at = (4, 5)
    s.next(MetroExit(found={}), T0)
    assert (s.pos, s.exit_at, s.grid.exits()) == ((4, 3), (4, 5), [(4, 5)])
    assert isinstance(s.next(at((4, 5), "stayed"), T0), Click)
    assert s.pos == (4, 5) and s.events[-1]["kind"] == "relocated"


def test_wall_answer_to_move_into_open_cell_halts() -> None:
    s = solver()
    assert s.next(at((1, 1)), T0) == Click("maze_down", "explore")
    assert s.next(at((1, 1), "wall"), T0) == Halt("unexpected_wall")
    assert s.events[-1] == {"step": 0, "pos": [1, 1], "kind": "wall", "direction": "down"}
    # Стена там, где карта её и показывает, или ход не наш — та же клетка, без остановки.
    known = solver()
    known.observe(at((1, 1)))
    known.observe(at((1, 1), "going", direction="up"))
    assert isinstance(known.next(at((1, 1), "wall"), T0), Click)
    manual = solver()
    manual.observe(at((1, 1)))
    assert isinstance(manual.next(at((1, 1), "wall"), T0), Click)


def test_heal_before_early_exit_counts_clicks_without_toast() -> None:
    # ❤️ (с тостом) и согласие, 🚪 и «Выйти» (без тоста): 2 + 6 + 6 + 2 = 16 с до выброса.
    kick = T0 + timedelta(minutes=10)
    b = Budget(T0, kick + timedelta(minutes=15), timedelta(minutes=25), 5.0)
    frame = at((1, 1), "waiting", stamina=60, packs=3)
    rushed = MetroSolver(POLICY, b, pos=(1, 1))
    assert rushed.next(frame, kick - timedelta(seconds=8)) == Click("maze_exit", "early_exit")
    healed = MetroSolver(POLICY, b, pos=(1, 1))
    assert healed.next(frame, kick - timedelta(seconds=16)) == Click("maze_first_aid", "heal")


def _on_exit_by_deadline(packs: int, policy: Policy = POLICY) -> MetroSolver:
    """Обход у выхода (ветка вправо не пройдена), ход на выход; битва через 40 минут."""
    s = MetroSolver(policy, budget(minutes=40, margin_min=25), pos=(4, 4))
    s.grid = grid_of(TREE)
    s.grid.visited = {(4, 4)}
    s.exit_at, s.stamina, s.packs = (4, 5), 60, packs
    s.observe(at((4, 4), "going", direction="right"))
    return s


def test_deadline_on_exit_screen_heals_before_leaving() -> None:
    """Уход по дедлайну решён на самом экране «Выходишь?», а на нём лечиться нельзя: остаться,
    долечиться, сойти с клетки и вернуться — и только тогда выйти."""
    s = _on_exit_by_deadline(packs=2)
    late = T0 + timedelta(minutes=15)
    assert s.next(MetroExit(found={}), late) == Click("maze_exit_decline", "heal_before_exit")
    assert (s.mode, s.leave_reason, s.pos) == ("leave", "deadline", (4, 5))
    assert s.next(at((4, 5), "stayed", stamina=60, packs=2), late) == Click(
        "maze_first_aid", "heal"
    )
    assert s.next(MetroFirstAid(packs=2, stamina=60, after=100), late) == Click(
        "maze_first_aid_accept", "heal"
    )
    assert s.next(at((4, 5), "none", stamina=100, packs=1), late) == Click("maze_left", "leave")
    move = s.next(at((4, 4), "arrived", direction="left", packs=1), late)
    assert move == Click("maze_right", "leave")
    assert s.next(MetroExit(found={}), late) == Click("maze_exit_accept", "deadline")


@pytest.mark.parametrize(
    ("packs", "heal_before_exit", "minutes"),
    [
        (0, True, 15),
        (2, False, 15),
        # Остаться, аптечка, два хода и «Выйти» — 31 с, а до выброса 30 с.
        (2, True, 24.5),
    ],
)
def test_deadline_on_exit_screen_leaves_at_once(
    packs: int, heal_before_exit: bool, minutes: float
) -> None:
    s = _on_exit_by_deadline(packs, policy_with(heal_before_exit=heal_before_exit))
    now = T0 + timedelta(minutes=minutes)
    assert s.next(MetroExit(found={}), now) == Click("maze_exit_accept", "deadline")


def test_replay_keeps_leaving_decided_before_restart() -> None:
    """До рестарта решатель ушёл к выходу по дедлайну. После рестарта путь до выхода короче, и
    по текущему моменту уход не сработал бы, — но он необратим: журнал повторяется с моментами
    кадров, и решатель по-прежнему идёт к выходу, а не возвращается к обходу."""
    start = T0 + timedelta(minutes=14)
    path = [(2, 1), (3, 1), (4, 1), (5, 1), (5, 2), (5, 3), (4, 3), (4, 4)]
    moves = ["down", "down", "down", "down", "right", "right", "up", "right"]
    now = start + timedelta(seconds=10)

    def known() -> MetroSolver:
        s = MetroSolver(POLICY, budget(minutes=40, margin_min=25), pos=(1, 1))
        s.grid = grid_of(TREE)
        s.grid.visited = {(1, 1)}
        s.exit_at = (4, 5)
        return s

    s = known()
    s.replay(at((1, 1), "waiting"), start)
    assert (s.mode, s.leave_reason) == ("leave", "deadline")
    for n, (pos, move) in enumerate(zip(path, moves, strict=True), 1):
        s.replay(at(pos, "arrived", direction=move), start + timedelta(seconds=n))
    s.update_mode(now)
    s.resync()
    assert s.next(at((4, 4), "waiting"), now) == Click("maze_right", "leave")
    fresh = known()
    fresh.pos = (4, 4)
    assert fresh.next(at((4, 4), "waiting"), now).reason == "explore"  # type: ignore[union-attr]


def test_unknown_cell_symbol_alerts_once() -> None:
    s = solver()
    odd = map_frame(("#####", "#####", "##@?.", "##.##", "##.##"), "entry")
    assert s.next(odd, T0) == Click("maze_down", "explore")
    assert s.alerts == ["unknown_cell"] and s.events[-1]["kind"] == "unknown_cell"
    s.next(map_frame(("#####", "#####", "#?@..", "##.##", "##.##"), "waiting"), T0)
    assert s.alerts == ["unknown_cell"]


def test_policy_comes_from_metro_settings() -> None:
    cfg = MetroSection(heal_at=40, npc_low_enabled=False, npc_high_enabled=True, chest_min_packs=3)
    assert policy_of(cfg) == Policy(
        heal_at=40,
        heal_before_exit=True,
        chest_min_packs=3,
        npc_low=False,
        npc_high=True,
        npc_min_stamina=30,
    )
