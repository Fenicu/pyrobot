"""Симулятор метро: лабиринт, игра на нём и кадры в настоящем текстовом формате игры."""

import random
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.engine.events import Event
from app.engine.metro.budget import Budget
from app.engine.metro.grid import DIRS, Pos, step
from app.engine.metro.solver import Click, Halt, MetroSolver, Move, Policy
from app.engine.parsing.metro import (
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
from app.engine.types import Button, IncomingMessage
from tests.engine.metro.helpers import T0

EMOJI = {"#": "⬛️", ".": "⬜️", "@": "\U0001f60e", "E": "\U0001f6aa"}
PAD = " " * 15
WORDS = {"up": "Вверх", "down": "Вниз", "left": "Влево", "right": "Вправо"}
FOOTER_WORDS = {
    "entry": "Вход",
    "waiting": "Ждёшь",
    "stayed": "Остался",
    "npc_declined": "Не бьёшся",
    "chest_declined": "Не открываешь",
    "wall": "Стена",
}
# Имя строки ресурса в блоках «Получено»/«Найдено» и хвост сообщения о находке.
ITEM_LINES = {
    "burger": "\U0001f354Бургер",
    "hotdog": "\U0001f32dХот-дог",
    "pizza": "\U0001f355Пицца",
    "money": "\U0001f4b5Деньги",
    "details": "⚙️Детали",
    "upgrades_white": "⚪️Улучшения",
    "knowledge": "\U0001f4daЗнания",
    "tokens": "\U0001f573Жетоны",
    "raw": "\U0001f529Сырьё",
}
LOOT_LINES = {
    "burger": ("", "\U0001f354", "Котлета - лучшая!"),
    "hotdog": ("", "\U0001f32d", "Съедобно."),
    "pizza": ("", "\U0001f355", "Черепашки оставили, не иначе."),
    "money": ("", "\U0001f4b5", "Богатеешь прямо на глазах."),
    "knowledge": ("", "\U0001f4da", "Становишься умнее."),
    "tokens": ("", "\U0001f573", "Покупай бафы на спуски."),
    "raw": ("+", "\U0001f529", "Всегда пригодится."),
}
NPC_TEXT = (
    "Ты нашёл \U0001f468Продавана в подземке. Что он тут делает ты выяснять не стал.\n"
    "Будешь сражаться?"
)
WON = "\U0001f44dБитва была жаркой, но тебе удалось победить!"
CHEST_TEXT = (
    "Отлично! Ты нашёл большой \U0001f4e6Сундук. Наверняка в нём много полезного. Попробуешь "
    "открыть? Это может быть ловушка! Но может просто чей-то тайник. Тебе решать, что делать."
)
OPENED = "Ты потихоньку открыл \U0001f4e6Сундук.\n"
ARROW = (
    "❗️Это ловушка! Вылетела стрела и ранила тебя в колено. \U0001f50bВыносливость "
    "упала до нуля.\nЕсли у тебя нет аптечек - тебе придётся выйти, потеряв половину "
    "найденного.\nЕсли есть аптечка - подлечись и иди дальше."
)
GRENADE = (
    "❗️Сработала замаскированная перечная граната. Пока ты чихал - растерял "
    "половину найденного в метро."
)
EXIT_HEAD = (
    "Ты нашёл выход из метро! Ты можешь покинуть метро со всем найденным скарбом, или "
    "походить ещё, поискать и вернуться в эту точку."
)
LEFT = "\U0001f50bОсталось выносливости: {}%"
EARLY_HEAD = "Ты собираешься досрочно покинуть метро.\nТы потеряешь половину найденного."


def _buttons(*rows: Iterable[tuple[str, str]]) -> tuple[Button, ...]:
    return tuple(
        Button(text, r, c, data=data)
        for r, row in enumerate(rows)
        for c, (text, data) in enumerate(row)
    )


def map_buttons(packs: int | None) -> tuple[Button, ...]:
    aid = f"❤️{packs}" if packs is not None else "❤️"
    return _buttons(
        ((" ", "maze_nothing"), ("⬆️", "maze_up"), (aid, "maze_first_aid")),
        (("⬅️", "maze_left"), (" ", "maze_nothing"), ("➡️", "maze_right")),
        (("\U0001f6ab", "maze_cancel_move"), ("⬇️", "maze_down"), ("\U0001f6aa", "maze_exit")),
    )


def _items(loot: dict[str, int]) -> str:
    return "".join(f"{ITEM_LINES[k]}: {v}\n" for k, v in loot.items())


Screen = tuple[str, tuple[Button, ...]]
CONTINUE = _buttons((("Продолжить", "maze_continue"),))


def render(event: Event, *, dot: bool = False) -> Screen:
    """Текст и кнопки экрана метро так, как их показывает игра."""
    if isinstance(event, MetroMap):
        rows = ["".join(EMOJI[c] for c in row) for row in event.window]
        if event.footer == "none":
            text = "\n".join(
                [f"\U0001f50b{event.stamina}%", *(r + PAD for r in rows[:-1]), rows[-1]]
            )
        else:
            if event.footer in ("going", "arrived"):
                word = WORDS[event.direction or ""]
                footer = f"Идёшь {word}{'.' if dot else ''}" if event.footer == "going" else word
            else:
                footer = FOOTER_WORDS[event.footer]
            text = "\n".join([f"\U0001f50b{event.stamina}%", *(r + PAD for r in rows), footer])
        return text, map_buttons(event.packs)
    if isinstance(event, MetroLoot):
        plus, emo, tail = LOOT_LINES[event.item]
        return f"{plus}Нашёл +{event.amount}{emo}. {tail}", CONTINUE
    if isinstance(event, MetroNpc):
        s = event.strength
        buttons = _buttons(
            (
                ("⚔Сразиться", f"maze_npc_{s}_accept"),
                ("\U0001f6b6Постоять рядом", f"maze_npc_{s}_decline"),
            )
        )
        return NPC_TEXT, buttons
    if isinstance(event, MetroFight):
        left = f"\n{LEFT.format(event.stamina)}" if event.stamina is not None else ""
        text = f"Ты сразился с {event.enemy}\n\n{WON}\n\nПолучено\n{_items(event.loot)}".rstrip(
            "\n"
        )
        return text + left, CONTINUE
    if isinstance(event, MetroChest):
        buttons = _buttons(
            (
                ("\U0001f44dОткрыть", "maze_chest_accept"),
                ("\U0001f44eОтказаться", "maze_chest_decline"),
            )
        )
        return CHEST_TEXT, buttons
    if isinstance(event, MetroChestOpened):
        body = {
            "arrow": ARROW,
            "grenade": GRENADE,
            "stash": "Это чей-то тайник!\n\nВнутри ты обнаружил\n"
            + _items(event.loot).rstrip("\n"),
        }[event.result]
        return OPENED + body, CONTINUE
    if isinstance(event, MetroFirstAid):
        text = (
            f"У тебя {event.packs}❤️ аптечек за \U0001f573.\n\nОдна аптечка "
            "восстанавливает твою \U0001f50bвыносливость на 50%, но не выше 100%.\n\n"
            f"У тебя \U0001f50b{event.stamina}%.\nПосле использования: {event.after}%\n\n"
            "Используешь аптечку?"
        )
        buttons = _buttons(
            (
                ("❤️Использую", "maze_first_aid_accept"),
                ("\U0001f44eОтказаться", "maze_first_aid_decline"),
            )
        )
        return text, buttons
    if isinstance(event, MetroExit):
        text = f"{EXIT_HEAD}\n\nНайдено\n{_items(event.found)}\nВыходишь?"
        buttons = _buttons(
            (("\U0001f44dВыйти", "maze_exit_accept"), ("\U0001f44eОстаться", "maze_exit_decline"))
        )
        return text, buttons
    if isinstance(event, MetroEarlyExit):
        text = (
            f"{EARLY_HEAD}\n\nНайдено\n{_items(event.found)}\nПолучишь половину\n"
            f"{_items(event.half)}\nВыходишь?"
        )
        buttons = _buttons(
            (("\U0001f44dВыйти", "maze_exit_accept"), ("\U0001f44eОстаться", "maze_exit_decline"))
        )
        return text, buttons
    if isinstance(event, MetroFinished):
        text = (
            f"Получено\n{_items(event.loot)}{LEFT.format(event.stamina)}\n\nК персонажу - /main."
        )
        return text, ()
    raise ValueError(f"no renderer for {event.kind}")


# --- лабиринты


@dataclass
class Maze:
    """Полная карта: `#` стена, `.` проход, `E` выход (за краем — стена) и спрятанные события."""

    cells: dict[Pos, str]
    start: Pos
    loot: dict[Pos, tuple[str, int]] = field(default_factory=dict)
    npcs: dict[Pos, str] = field(default_factory=dict)
    chests: dict[Pos, str] = field(default_factory=dict)

    def get(self, pos: Pos) -> str:
        return self.cells.get(pos, "#")

    def passable(self, pos: Pos) -> bool:
        return self.get(pos) in (".", "E")

    def floor(self) -> set[Pos]:
        return {p for p, sym in self.cells.items() if sym in (".", "E")}

    @property
    def exit(self) -> Pos:
        return next(p for p, sym in self.cells.items() if sym == "E")

    def window(self, pos: Pos) -> tuple[str, ...]:
        return tuple(
            "".join(
                "@" if (dr, dc) == (0, 0) else self.get((pos[0] + dr, pos[1] + dc))
                for dc in range(-2, 3)
            )
            for dr in range(-2, 3)
        )

    def edges(self) -> int:
        floor = self.floor()
        return sum(1 for p in floor for d in ("down", "right") if step(p, d) in floor)

    def distances(self, origin: Pos) -> dict[Pos, int]:
        dist = {origin: 0}
        queue = deque([origin])
        while queue:
            cur = queue.popleft()
            for d in DIRS:
                nxt = step(cur, d)
                if nxt not in dist and self.passable(nxt):
                    dist[nxt] = dist[cur] + 1
                    queue.append(nxt)
        return dist


def tree_maze(rows: int, cols: int, rng: random.Random) -> Maze:
    """Совершенный лабиринт (дерево) на решётке узлов `rows × cols`, коридоры шириной 1."""
    cells = {(r, c): "#" for r in range(2 * rows + 1) for c in range(2 * cols + 1)}
    start = (2 * rng.randrange(rows) + 1, 2 * rng.randrange(cols) + 1)
    cells[start] = "."
    stack = [start]
    while stack:
        cur = stack[-1]
        options = [
            (d, (cur[0] + 2 * dr, cur[1] + 2 * dc))
            for d, (dr, dc) in DIRS.items()
            if cells.get((cur[0] + 2 * dr, cur[1] + 2 * dc)) == "#"
        ]
        if not options:
            stack.pop()
            continue
        d, nxt = rng.choice(options)
        cells[step(cur, d)] = "."
        cells[nxt] = "."
        stack.append(nxt)
    maze = Maze(cells, start)
    _place_exit(maze, rng)
    return maze


def loopy_maze(rows: int, cols: int, rng: random.Random, extra: float = 0.15) -> Maze:
    """Дерево, в котором снесена доля `extra` оставшихся внутренних стен между узлами: циклы."""
    maze = tree_maze(rows, cols, rng)
    walls = [
        p
        for p, sym in maze.cells.items()
        if sym == "#" and 0 < p[0] < 2 * rows and 0 < p[1] < 2 * cols and (p[0] % 2) != (p[1] % 2)
    ]
    for p in rng.sample(walls, int(len(walls) * extra)):
        maze.cells[p] = "."
    return maze


def corridor_maze(length: int) -> Maze:
    """Прямой коридор: все окна в середине одинаковы."""
    cells = {(r, c): "#" for r in range(3) for c in range(length + 2)}
    for c in range(1, length + 1):
        cells[(1, c)] = "."
    cells[(1, length)] = "E"
    return Maze(cells, (1, length // 2))


def _place_exit(maze: Maze, rng: random.Random) -> None:
    nodes = [p for p, sym in maze.cells.items() if sym == "." and p != maze.start]
    far = maze.distances(maze.start)
    # Выход — случайно среди дальней половины клеток: и в глубине, и не всегда в тупике.
    ranked = sorted(nodes, key=lambda p: far[p])
    maze.cells[rng.choice(ranked[len(ranked) // 2 :])] = "E"


def hide_events(
    maze: Maze, rng: random.Random, *, loot: int = 12, npcs: int = 3, chests: int = 3
) -> Maze:
    free = sorted(p for p, sym in maze.cells.items() if sym == "." and p != maze.start)
    picks = rng.sample(free, min(len(free), loot + npcs + chests))
    items = list(LOOT_LINES)
    for p in picks[:loot]:
        maze.loot[p] = (rng.choice(items), rng.randint(1, 80))
    for p in picks[loot : loot + npcs]:
        maze.npcs[p] = "low"
    for p in picks[loot + npcs :]:
        maze.chests[p] = rng.choice(("stash", "arrow", "grenade"))
    return maze


# --- игра


@dataclass(frozen=True)
class Frame:
    """Правка сообщения метро: событие и точка в «Идёшь …» (игра ставит её, если прошлый кадр —
    приход в том же направлении)."""

    event: Event
    dot: bool = False

    def screen(self) -> Screen:
        return render(self.event, dot=self.dot)


@dataclass
class SimClock:
    """Время симуляции: клик — темп шлюза, ход — ещё и переход (быстрый шаг)."""

    t: float = 0.0
    click_s: float = 1.8
    move_s: float = 2.0


@dataclass
class MazeGame:
    """Игровая сторона метро на лабиринте: клик → правки сообщения (как экраны игры)."""

    maze: Maze
    stamina: int = 88
    packs: int = 7
    npc_costs: tuple[int, ...] = (33, 11, 0)
    clock: SimClock = field(default_factory=SimClock)
    # Номера ходов, на которых игра «не сдвинула» игрока, но показала подпись прихода.
    glitch_moves: frozenset[int] = frozenset()
    pos: Pos = (0, 0)
    bank: dict[str, int] = field(default_factory=dict)
    screen: Event | None = None
    moves: int = 0
    clicks: int = 0
    finished: bool = False
    early_exit: bool = False
    wall_hits: int = 0
    triggered: set[Pos] = field(default_factory=set)
    _last_footer: tuple[str, str | None] = ("entry", None)
    _fights: int = 0

    def __post_init__(self) -> None:
        self.pos = self.maze.start

    def start(self) -> Event:
        self.screen = self._map("entry")
        return self.screen

    def _map(self, footer: str, direction: str | None = None) -> MetroMap:
        self._last_footer = (footer, direction)
        return MetroMap(
            stamina=self.stamina,
            window=self.maze.window(self.pos),
            footer=footer,  # type: ignore[arg-type]
            direction=direction,
            packs=self.packs,
        )

    def _add(self, loot: dict[str, int]) -> None:
        for k, v in loot.items():
            self.bank[k] = self.bank.get(k, 0) + v

    def click(self, data: str) -> list[Frame]:
        """Правки сообщения в ответ на клик; последняя — текущий экран."""
        self.clicks += 1
        self.clock.t += self.clock.click_s
        screen = self.screen
        out: list[Event | Frame]
        if isinstance(screen, MetroMap) and data.removeprefix("maze_") in DIRS:
            out = self._move(data.removeprefix("maze_"))
        elif isinstance(screen, MetroMap) and data == "maze_first_aid" and self.packs > 0:
            out = [
                MetroFirstAid(
                    packs=self.packs, stamina=self.stamina, after=min(100, self.stamina + 50)
                )
            ]
        elif isinstance(screen, MetroFirstAid):
            if data == "maze_first_aid_accept":
                self.stamina, self.packs = min(100, self.stamina + 50), self.packs - 1
            out = [self._map("none")]
        elif (
            isinstance(screen, (MetroLoot, MetroFight, MetroChestOpened))
            and data == "maze_continue"
        ):
            out = [self._map("waiting")]
        elif isinstance(screen, MetroMap) and data == "maze_exit":
            half = {k: v - v // 2 for k, v in self.bank.items()}
            out = [MetroEarlyExit(found=dict(self.bank), half=half)]
        elif isinstance(screen, MetroNpc):
            fight = data.endswith("_accept")
            out = [self._fight()] if fight else [self._map("npc_declined")]
        elif isinstance(screen, MetroChest):
            opened = data == "maze_chest_accept"
            out = [self._chest()] if opened else [self._map("chest_declined")]
        elif isinstance(screen, MetroExit | MetroEarlyExit):
            if data == "maze_exit_accept":
                # Итог досрочного выхода живьём не видели: считаем его обычным «Получено».
                self.finished = True
                self.early_exit = isinstance(screen, MetroEarlyExit)
                loot = screen.half if isinstance(screen, MetroEarlyExit) else dict(self.bank)
                out = [MetroFinished(loot=loot, stamina=self.stamina)]
            else:
                out = [self._map("stayed")]
        else:
            raise AssertionError(f"click {data} on {screen}")
        frames = [f if isinstance(f, Frame) else Frame(f) for f in out]
        self.screen = frames[-1].event
        if not isinstance(self.screen, MetroMap):
            self._last_footer = ("event", None)
        return frames

    def _move(self, direction: str) -> list[Event | Frame]:
        target = step(self.pos, direction)
        if not self.maze.passable(target):
            # Игра: тост «⬛️Там стена» и кадр со старым окном, без «Идёшь …».
            self.wall_hits += 1
            return [self._map("wall")]
        self.moves += 1
        self.clock.t += self.clock.move_s
        going = Frame(
            MetroMap(
                stamina=self.stamina,
                window=self.maze.window(self.pos),
                footer="going",
                direction=direction,
                packs=self.packs,
            ),
            dot=self._last_footer == ("arrived", direction),
        )
        if self.moves in self.glitch_moves:
            return [going, self._map("arrived", direction)]
        self.pos = target
        event = self._cell_event()
        if event is not None:
            return [going, event]
        return [going, self._map("arrived", direction)]

    def _cell_event(self) -> Event | None:
        pos = self.pos
        if self.maze.get(pos) == "E":
            return MetroExit(found=dict(self.bank))
        if pos in self.triggered:
            return None
        if pos in self.maze.loot:
            self.triggered.add(pos)
            item, amount = self.maze.loot[pos]
            self._add({item: amount})
            return MetroLoot(item=item, amount=amount)
        if pos in self.maze.npcs:
            self.triggered.add(pos)
            return MetroNpc(strength="high" if self.maze.npcs[pos] == "high" else "low")
        if pos in self.maze.chests:
            self.triggered.add(pos)
            return MetroChest()
        return None

    def _fight(self) -> MetroFight:
        cost = self.npc_costs[self._fights % len(self.npc_costs)]
        self._fights += 1
        self.stamina = max(0, self.stamina - cost)
        loot = {"money": 60, "details": 8, "upgrades_white": 1}
        self._add(loot)
        return MetroFight(
            enemy="\U0001f468Продаваном \U0001f468Иван (71)",
            won=True,
            loot=loot,
            stamina=self.stamina,
        )

    def _chest(self) -> MetroChestOpened:
        outcome = self.maze.chests[self.pos]
        if outcome == "arrow":
            self.stamina = 0
            return MetroChestOpened(result="arrow")
        if outcome == "grenade":
            self.bank = {k: v - v // 2 for k, v in self.bank.items()}
            return MetroChestOpened(result="grenade")
        loot = {"burger": 2, "tokens": 23, "raw": 8}
        self._add(loot)
        return MetroChestOpened(result="stash", loot=loot)


# --- прогон решателя на симуляторе


@dataclass
class Drive:
    solver: MetroSolver
    game: MazeGame
    outcome: Move
    finished_at: datetime
    # Ходы, сделанные при 🔋 не выше порога, когда аптечки ещё были.
    heal_misses: int = 0


def message(frame: Frame, msg_id: int = 1) -> IncomingMessage:
    text, buttons = frame.screen()
    return text_message(text, buttons, msg_id)


def text_message(text: str, buttons: tuple[Button, ...] = (), msg_id: int = 1) -> IncomingMessage:
    return IncomingMessage(
        chat_id=227859379,
        msg_id=msg_id,
        revision=0,
        kind="edit",
        date=T0,
        received_at=T0,
        text=text,
        inline=buttons,
    )


def parsed(frame: Frame) -> Event:
    """Экран проходит через настоящий текстовый формат и настоящий распознаватель."""
    [event] = recognize_metro(message(frame))
    return event


def drive(
    maze: Maze,
    *,
    policy: Policy | None = None,
    battle_in: timedelta | None = None,
    margin: timedelta = timedelta(minutes=25),
    max_clicks: int = 20_000,
    **game: Any,
) -> Drive:
    sim = MazeGame(maze, **game)
    battle = T0 + battle_in if battle_in is not None else None
    policy = policy or Policy()
    solver = MetroSolver(policy, Budget(T0, battle, margin, 5.0), pos=maze.start)
    event: Event = sim.start()
    misses = 0
    outcome: Move = Halt("max_clicks")
    for _ in range(max_clicks):
        now = T0 + timedelta(seconds=sim.clock.t)
        outcome = solver.next(parsed(Frame(event)), now)
        if not isinstance(outcome, Click):
            break
        is_move = outcome.data.removeprefix("maze_") in DIRS
        low = solver.stamina is not None and solver.stamina <= policy.heal_at
        if is_move and low and solver.packs:
            misses += 1
        event = sim.click(outcome.data)[-1].event
    return Drive(solver, sim, outcome, T0 + timedelta(seconds=sim.clock.t), misses)
