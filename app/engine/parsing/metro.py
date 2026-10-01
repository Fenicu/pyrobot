from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar, Literal

from app.engine.events import Event
from app.engine.parsing.common import DURATION, NUM, dur, num
from app.engine.parsing.refusals import Refused
from app.engine.types import IncomingMessage

# Клетки окна карты: стена, проход, игрок, выход; незнакомый символ — «?».
WALL, FLOOR, ME, EXIT, OTHER = "#", ".", "@", "E", "?"
_CELLS = {"⬛": WALL, "⬜": FLOOR, "😎": ME, "🚪": EXIT}
WINDOW = 5
VS16 = "\ufe0f"
DIRECTIONS = {"Вверх": "up", "Вниз": "down", "Влево": "left", "Вправо": "right"}
# Вход стоит 2🔥 (экран входа); бафы экрана входа не показывают цену входа.
ENTRY_COST = 2
# Спуститься в метро снова можно через 16 ч после выхода (экран входа).
METRO_COOLDOWN = timedelta(hours=16)
BUFFS = {"🏃Быстрый шаг": "fastMove", "💪Страшная сила": "strong", "❤️Аптечки": "firstAid"}
# Ключи — без вариационного селектора U+FE0F: игра ставит его непостоянно.
ITEMS = {
    "🍔": "burger",
    "🌭": "hotdog",
    "🍕": "pizza",
    "🍌": "banana",
    "💵": "money",
    "⚙": "details",
    "🔩": "raw",
    "📚": "knowledge",
    "💡": "exp",
    "🕳": "tokens",
    "⚪": "upgrades_white",
    "🔵": "upgrades_blue",
    "🔴": "upgrades_red",
}

_ENTRANCE = re.compile(
    r"\AТы у входа в давно заброшенные ветки 🚇Метро\.\n.*?"
    r"^Вход в метро требует (?P<cost>\d+)🔥Мотивации\.\nУ тебя (?P<mot>\d+)🔥\.\Z",
    re.S | re.M,
)
_COOLDOWN = re.compile(
    r"\AТы уже побывал в метро недавно\. До следующего спуска (?P<t>" + DURATION + r")"
)
_BUFFS = re.compile(
    r"\AБафы для метро\n.*?^У тебя\n🌐Sw-coin: (?P<coins>" + NUM + r")\n"
    r"🕳Жетоны: (?P<tokens>" + NUM + r")\Z",
    re.S | re.M,
)
_BOUGHT = re.compile(r"^Куплены\n(?P<lines>(?:[^\n]+\n)+)\n", re.M)
_BUFF_LINE = re.compile(r"\A(?P<name>.+?) за \d+(?:🕳|🌐)\Z")
_TOKEN_PRICE = re.compile(r"^— \S+ за (?P<n>\d+)🕳", re.M)
_STAMINA = re.compile(r"\A🔋(?P<st>\d+)%\Z")
_FOOTER = re.compile(
    r"\A(?:(?P<going>Идёшь )?(?P<dir>Вверх|Вниз|Влево|Вправо)(?(going)\.?)"
    r"|(?P<word>Вход|Ждёшь|Остался|Не бьёшся|Не открываешь|Стена))\Z"
)
_PACKS = re.compile(r"\A❤️(?P<n>\d+)\Z")
_LOOT = re.compile(r"\A\+?Нашёл \+(?P<n>\d+)(?P<emo>[^\w\s.]+)\. [^\n]+\Z")
_ITEM_LINE = re.compile(r"^(?P<emo>[^\w\s:]+)[А-ЯЁа-яё][^\n:]*: (?P<n>" + NUM + r")$", re.M)
_FIGHT = re.compile(
    r"\AТы сразился с (?P<enemy>[^\n]+)\n\n(?P<verdict>[^\n]+)\n\n"
    r"(?:Получено\n|(?=🔋Осталось выносливости))",
    re.S,
)
_WON = "удалось победить"
_LEFT_STAMINA = re.compile(r"^🔋Осталось выносливости: (?P<st>\d+)%$", re.M)
_CHEST = re.compile(r"\AОтлично! Ты нашёл большой 📦Сундук\. [^\n]+\Z")
_OPENED = "Ты потихоньку открыл 📦Сундук.\n"
_STASH = re.compile(r"\AЭто чей-то тайник!\n\nВнутри ты обнаружил\n(?:[^\n]+: \d+\n?)+\Z")
_ARROW = re.compile(r"\A❗️Это ловушка! Вылетела стрела[^\n]*🔋Выносливость упала до нуля\.\n")
_GRENADE = re.compile(r"\A❗️Сработала замаскированная перечная граната\.")
_FIRST_AID = re.compile(
    r"\AУ тебя (?P<packs>\d+)❤️ аптечек за 🕳\.\n\n.*?^У тебя 🔋(?P<st>\d+)%\.\n"
    r"После использования: (?P<after>\d+)%\n\nИспользуешь аптечку\?\Z",
    re.S | re.M,
)
_EXIT = re.compile(
    r"\AТы нашёл выход из метро! [^\n]+\n\nНайдено\n(?P<items>(?:[^\n]+\n)*)\nВыходишь\?\Z"
)
_EARLY_EXIT = re.compile(
    r"\AТы собираешься досрочно покинуть метро\.\nТы потеряешь половину найденного\.\n\n"
    r"Найдено\n(?P<found>(?:[^\n]+\n)*)\nПолучишь половину\n(?P<half>(?:[^\n]+\n)*)\n"
    r"Выходишь\?\Z"
)
_FINISHED = re.compile(
    r"\AПолучено\n(?P<items>(?:[^\n]+\n)*?)🔋Осталось выносливости: (?P<st>\d+)%\n\n"
    r"К персонажу - /main\.\Z"
)

Footer = Literal[
    "entry",
    "arrived",
    "going",
    "waiting",
    "stayed",
    "npc_declined",
    "chest_declined",
    "wall",
    "none",
]
ChestOutcome = Literal["stash", "arrow", "grenade"]
# Все подписи, кроме прихода (`arrived`), значат «персонаж на той же клетке».
_WORDS: dict[str, Footer] = {
    "Вход": "entry",
    "Ждёшь": "waiting",
    "Остался": "stayed",
    # Отказ от боя с NPC и от сундука: персонаж остаётся на их клетке.
    "Не бьёшся": "npc_declined",
    "Не открываешь": "chest_declined",
    # Ход в стену не состоялся (тост «⬛️Там стена», подпись «Идёшь …» не приходит).
    "Стена": "wall",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class MetroEntrance(Event):
    kind: ClassVar[str] = "metro_entrance"
    cost: int
    motivation: int


@dataclass(frozen=True, slots=True, kw_only=True)
class MetroBuffs(Event):
    """Экран бафов: что куплено, какие бафы за 🕳 ещё можно купить (кнопки), жетоны."""

    kind: ClassVar[str] = "metro_buffs"
    bought: tuple[str, ...]
    offers: tuple[str, ...]
    tokens: int
    coins: int
    token_price: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MetroEntered(Event):
    """Вход принят (экран бафов после `maze_enter_accept`): списывается цена входа."""

    kind: ClassVar[str] = "metro_entered"
    outcome: ClassVar[bool] = True
    cost: int = ENTRY_COST


@dataclass(frozen=True, slots=True, kw_only=True)
class MetroMap(Event):
    """Кадр карты: окно 5×5 (игрок в центре), 🔋, аптечки с кнопки ❤️N и подпись."""

    kind: ClassVar[str] = "metro_map"
    stamina: int
    window: tuple[str, ...]
    footer: Footer
    direction: str | None = None
    packs: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MetroLoot(Event):
    kind: ClassVar[str] = "metro_loot"
    item: str
    amount: int


@dataclass(frozen=True, slots=True, kw_only=True)
class MetroNpc(Event):
    kind: ClassVar[str] = "metro_npc"
    strength: Literal["low", "high"]


@dataclass(frozen=True, slots=True, kw_only=True)
class MetroFight(Event):
    kind: ClassVar[str] = "metro_fight"
    enemy: str
    won: bool
    loot: dict[str, int] = field(default_factory=dict)
    stamina: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MetroChest(Event):
    kind: ClassVar[str] = "metro_chest"


@dataclass(frozen=True, slots=True, kw_only=True)
class MetroChestOpened(Event):
    kind: ClassVar[str] = "metro_chest_opened"
    result: ChestOutcome
    loot: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class MetroFirstAid(Event):
    kind: ClassVar[str] = "metro_first_aid"
    packs: int
    stamina: int
    after: int


@dataclass(frozen=True, slots=True, kw_only=True)
class MetroExit(Event):
    """Найден выход: предложение выйти со всем найденным."""

    kind: ClassVar[str] = "metro_exit"
    found: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class MetroEarlyExit(Event):
    """Досрочный выход кнопкой 🚪 с карты: сколько найдено и сколько достанется (половина)."""

    kind: ClassVar[str] = "metro_early_exit"
    found: dict[str, int] = field(default_factory=dict)
    half: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class MetroFinished(Event):
    """Итог забега «Получено … Осталось выносливости»: начисляется один раз."""

    kind: ClassVar[str] = "metro_finished"
    outcome: ClassVar[bool] = True
    loot: dict[str, int] = field(default_factory=dict)
    stamina: int


def tokens(line: str) -> list[str]:
    """Символы строки окна: вариационные селекторы и модификаторы — к предыдущему символу."""
    out: list[str] = []
    join = False
    for ch in line.rstrip():
        cp = ord(ch)
        if ch == " ":
            continue
        if out and (cp == 0xFE0F or 0x1F3FB <= cp <= 0x1F3FF):
            out[-1] += ch
            continue
        if out and cp == 0x200D:
            out[-1] += ch
            join = True
            continue
        if join:
            out[-1] += ch
            join = False
            continue
        out.append(ch)
    return [t.replace(VS16, "") for t in out]


def parse_window(lines: list[str]) -> tuple[str, ...] | None:
    rows: list[str] = []
    for line in lines:
        cells = tokens(line)
        if len(cells) != WINDOW:
            return None
        rows.append("".join(_CELLS.get(c, OTHER) for c in cells))
    center = WINDOW // 2
    if len(rows) != WINDOW or rows[center][center] != ME or "".join(rows).count(ME) != 1:
        return None
    return tuple(rows)


def item_name(emo: str) -> str:
    """Имя ресурса по эмодзи; незнакомый эмодзи остаётся как есть."""
    bare = emo.replace(VS16, "")
    return ITEMS.get(bare, bare)


def items(block: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for m in _ITEM_LINE.finditer(block):
        key = item_name(m["emo"])
        found[key] = found.get(key, 0) + num(m["n"])
    return found


def _packs(msg: IncomingMessage) -> int | None:
    button = msg.button("maze_first_aid")
    if button is None:
        return None
    m = _PACKS.match(button.text)
    return int(m["n"]) if m else None


def _map(msg: IncomingMessage, text: str) -> list[Event]:
    lines = text.split("\n")
    if len(lines) not in (1 + WINDOW, 2 + WINDOW):
        return []
    stamina = _STAMINA.match(lines[0])
    window = parse_window(lines[1 : 1 + WINDOW])
    if stamina is None or window is None:
        return []
    footer: Footer = "none"
    direction = None
    if len(lines) == 2 + WINDOW:
        m = _FOOTER.match(lines[-1])
        if m is None:
            return []
        if m["dir"]:
            footer = "going" if m["going"] else "arrived"
            direction = DIRECTIONS[m["dir"]]
        else:
            footer = _WORDS[m["word"]]
    return [
        MetroMap(
            stamina=int(stamina["st"]),
            window=window,
            footer=footer,
            direction=direction,
            packs=_packs(msg),
        )
    ]


def _buffs(msg: IncomingMessage, text: str) -> list[Event]:
    m = _BUFFS.match(text)
    if m is None:
        return []
    bought: list[str] = []
    if block := _BOUGHT.search(text):
        for line in block["lines"].splitlines():
            name = _BUFF_LINE.match(line)
            if name is None or name["name"] not in BUFFS:
                return []
            bought.append(BUFFS[name["name"]])
    prefix = "maze_buf_tokens_"
    offers = tuple(
        b.data.removeprefix(prefix) for b in msg.inline if b.data and b.data.startswith(prefix)
    )
    price = _TOKEN_PRICE.search(text)
    screen = MetroBuffs(
        bought=tuple(bought),
        offers=offers,
        tokens=num(m["tokens"]),
        coins=num(m["coins"]),
        token_price=int(price["n"]) if price else None,
    )
    return [screen, MetroEntered()]


def _chest_opened(text: str) -> list[Event]:
    rest = text.removeprefix(_OPENED)
    if _STASH.match(rest):
        return [MetroChestOpened(result="stash", loot=items(rest))]
    if _ARROW.match(rest):
        return [MetroChestOpened(result="arrow")]
    if _GRENADE.match(rest):
        return [MetroChestOpened(result="grenade")]
    return []


def recognize_metro(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if text.startswith("🔋"):
        return _map(msg, text)
    if m := _ENTRANCE.match(text):
        return [MetroEntrance(cost=int(m["cost"]), motivation=int(m["mot"]))]
    if m := _COOLDOWN.match(text):
        return [Refused(reason="metro_cooldown", left_s=dur(m["t"]))]
    if text.startswith("Бафы для метро\n"):
        return _buffs(msg, text)
    if m := _LOOT.match(text):
        return [MetroLoot(item=item_name(m["emo"]), amount=int(m["n"]))]
    # Сильный NPC живьём не встречен: узнаём его только по кнопкам (callback из старого кода).
    if msg.button("maze_npc_low_accept") and msg.button("maze_npc_low_decline"):
        return [MetroNpc(strength="low")]
    if msg.button("maze_npc_high_accept") and msg.button("maze_npc_high_decline"):
        return [MetroNpc(strength="high")]
    if m := _FIGHT.match(text):
        left = _LEFT_STAMINA.search(text)
        return [
            MetroFight(
                enemy=m["enemy"],
                won=_WON in m["verdict"],
                loot=items(text[m.end() :]),
                stamina=int(left["st"]) if left else None,
            )
        ]
    if _CHEST.match(text) and msg.button("maze_chest_accept"):
        return [MetroChest()]
    if text.startswith(_OPENED):
        return _chest_opened(text)
    if m := _FIRST_AID.match(text):
        return [MetroFirstAid(packs=int(m["packs"]), stamina=int(m["st"]), after=int(m["after"]))]
    if m := _EXIT.match(text):
        return [MetroExit(found=items(m["items"]))]
    if m := _EARLY_EXIT.match(text):
        return [MetroEarlyExit(found=items(m["found"]), half=items(m["half"]))]
    if m := _FINISHED.match(text):
        return [MetroFinished(loot=items(m["items"]), stamina=int(m["st"]))]
    return []


RECOGNIZERS = (recognize_metro,)
