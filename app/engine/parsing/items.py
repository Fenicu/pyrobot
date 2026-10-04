from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field, replace
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import DURATION, NUM, SKILLS, Rewards, dur, num, parse_rewards
from app.engine.types import IncomingMessage

_INVENTORY = "Гаджеты при тебе: (снять)"
_BOOKS = re.compile(r"^📒Книга опыта: (?P<n>\d+)(?: \((?P<t>" + DURATION + r")\))?", re.M)
_CARDS = re.compile(r"^💳Подарочная карта: (?P<n>\d+)(?: \((?P<t>" + DURATION + r")\))?", re.M)
_VS16 = "\ufe0f"
_SET = re.compile(r"^\S+?Сет ")
# Строка надетого гаджета: «⚫️26 🕶Хиджаб (+85🎓, 🧶) /unwear_h18»; значок слота слитно с названием,
# у неулучшенного гаджета редкости и уровня нет: «👔Жилетка LoRat (+63🐢, +23🎓) /unwear_t501».
_GADGET = re.compile(
    r"^(?:(?P<grade>[^\d\s]+)(?P<level>\d+)[ \xa0])?(?P<slot>[^\w\s]\ufe0f?)(?P<name>.+?)"
    r" \((?P<stats>[^()]*)\) /unwear_\w+$"
)
_BONUS = re.compile(r"^\+(?P<n>\d+)(?P<icon>\S+)$")
_SKILL_BY_ICON = {name[0]: code for name, code in SKILLS.items()}
_PRIZEBOX = re.compile(
    r"^🎁[\xa0 ]?Призовая коробка /unbox(?: \((?P<t>" + DURATION + r")\))?$", re.M
)
_SLOTS = re.compile(r"^Занято (?P<used>\d+) из (?P<cap>\d+)$", re.M)
_BOOK = re.compile(
    r"\AТы прочёл 📒Книгу и получил:\n💡Опыт: \+(?P<exp>\d+)\n\n"
    r"Следующую книгу можно прочесть через (?P<t>[^\n]+)"
)
_CARD = re.compile(
    r"\AТы воспользовался 💳Подарочной картой и получил:\n💵: \+(?P<money>\d+)\$\n\n"
    r"Следующую карту можно использовать через (?P<t>[^\n]+)"
)
_GIFTS = re.compile(
    r"\A🗳Контейнеры:\nМалые: (?P<small>\d+)\n(?:/unbox_ls\n)?Средние: (?P<medium>\d+)"
)
_GIFTS_TANGERINES = re.compile(r"🍊У тебя: (?P<n>\d+) шт")
_CONTAINER = re.compile(r"\AТы открыл (?P<size>Малый|Средний) 🗳контейнер")
_CONTENTS = re.compile(r"^Внутри ты обнаружил:\n(?P<body>.*?)(?:\n\n|\Z)", re.M | re.S)
# Предмет в контейнере: «Флюс», «Пьезодинамик - 3 шт.»; строки с двоеточием — ресурсы и улучшения.
_CONTENT_ITEM = re.compile(r"\A(?P<name>[^:]*?[^\s:])(?: - (?P<n>\d+) шт\.)?\Z")
_PRIZEBOX_OPENED = "👍Ура! Тебе удалось наконец-то открыть призовую коробку."
_MONEY_AFTER = re.compile(r"Стало: \$(?P<money>" + NUM + r")")


@dataclass(frozen=True, slots=True, kw_only=True)
class Gadget:
    """Надетый гаджет: значок редкости и уровень (у неулучшенного их нет), значок слота, название,
    бонусы по навыкам (`practice`, `theory`, `cunning`, `wisdom`) и метка в конце скобок
    (🧶, 📿, 💎)."""

    grade: str | None
    level: int | None
    slot: str
    name: str
    bonuses: dict[str, int]
    mark: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Gadgets:
    """Надетые гаджеты в порядке экрана и строки сетов как есть («⚫️Сет VIP»)."""

    items: tuple[Gadget, ...] = ()
    sets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Inventory(Event):
    kind: ClassVar[str] = "inventory"
    books: int
    books_in_s: int | None
    cards: int
    cards_in_s: int | None
    prizebox: bool
    prizebox_in_s: int | None
    bag: int
    bag_cap: int
    gadgets: Gadgets = field(default_factory=Gadgets)


@dataclass(frozen=True, slots=True, kw_only=True)
class BookRead(Event):
    kind: ClassVar[str] = "book_read"
    outcome: ClassVar[bool] = True
    exp: int
    next_in_s: int


@dataclass(frozen=True, slots=True, kw_only=True)
class CardUsed(Event):
    kind: ClassVar[str] = "card_used"
    outcome: ClassVar[bool] = True
    money: int
    next_in_s: int


@dataclass(frozen=True, slots=True, kw_only=True)
class GiftsScreen(Event):
    kind: ClassVar[str] = "gifts_screen"
    containers_small: int
    containers_medium: int
    tangerines: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ContainerOpened(Event):
    """Открыт контейнер: содержимое — ресурсы, улучшения и предметы крафта (`rewards.items`)."""

    kind: ClassVar[str] = "container_opened"
    outcome: ClassVar[bool] = True
    size: str
    rewards: Rewards = field(default_factory=Rewards)


@dataclass(frozen=True, slots=True, kw_only=True)
class PrizeboxOpened(Event):
    """Открыта призовая коробка: явная прибавка («💵Деньги: +$300», опыт, улучшения) и снимок
    денег после неё («Стало: $…»), если коробка дала деньги."""

    kind: ClassVar[str] = "prizebox_opened"
    outcome: ClassVar[bool] = True
    money_after: int | None
    rewards: Rewards = field(default_factory=Rewards)


def _count(m: re.Match[str] | None) -> tuple[int, int | None]:
    if m is None:
        return 0, None
    return int(m["n"]), dur(m["t"]) if m["t"] else None


def _gadget(line: str) -> Gadget | None:
    m = _GADGET.match(line)
    if m is None:
        return None
    bonuses: dict[str, int] = {}
    mark: str | None = None
    for part in m["stats"].split(", "):
        bonus = _BONUS.match(part)
        if bonus is None:
            mark = part or None
            continue
        code = _SKILL_BY_ICON.get(bonus["icon"].replace(_VS16, ""))
        if code is None:
            return None
        bonuses[code] = int(bonus["n"])
    return Gadget(
        grade=m["grade"],
        level=int(m["level"]) if m["level"] else None,
        slot=m["slot"],
        name=m["name"],
        bonuses=bonuses,
        mark=mark,
    )


def _gadgets(text: str) -> Gadgets:
    """Блок «Гаджеты при тебе»: строки гаджетов до пустой, затем строки сетов до первой строки не
    сета; блок рюкзака не читается. Нераспознанная строка гаджета пропускается."""
    lines = text.split("\n")[1:]
    worn = list(itertools.takewhile(str.strip, lines))
    rest = lines[len(worn) + 1 :]
    sets = itertools.takewhile(_SET.match, rest)
    items = (_gadget(line) for line in worn)
    return Gadgets(
        items=tuple(g for g in items if g is not None), sets=tuple(s.strip() for s in sets)
    )


def _inventory(text: str) -> list[Event]:
    # Строк 📒 и 💳 при нуле нет: целостность экрана определяют заголовок и «Занято N из M».
    slots = _SLOTS.search(text)
    if slots is None:
        return []
    books, books_in_s = _count(_BOOKS.search(text))
    cards, cards_in_s = _count(_CARDS.search(text))
    box = _PRIZEBOX.search(text)
    return [
        Inventory(
            books=books,
            books_in_s=books_in_s,
            cards=cards,
            cards_in_s=cards_in_s,
            prizebox=box is not None,
            prizebox_in_s=dur(box["t"]) if box and box["t"] else None,
            bag=int(slots["used"]),
            bag_cap=int(slots["cap"]),
            gadgets=_gadgets(text),
        )
    ]


def _contents(text: str) -> Rewards:
    block = _CONTENTS.search(text)
    if block is None:
        return Rewards()
    body = block["body"]
    items: dict[str, int] = {}
    for line in body.split("\n"):
        if (m := _CONTENT_ITEM.match(line.strip())) is not None:
            items[m["name"]] = items.get(m["name"], 0) + int(m["n"] or 1)
    return replace(parse_rewards(body), items=items)


def recognize_items(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if text.startswith(_INVENTORY):
        return _inventory(text)
    if m := _BOOK.match(text):
        return [BookRead(exp=int(m["exp"]), next_in_s=dur(m["t"]))]
    if m := _CARD.match(text):
        return [CardUsed(money=int(m["money"]), next_in_s=dur(m["t"]))]
    if m := _GIFTS.match(text):
        tangerines = _GIFTS_TANGERINES.search(text)
        return [
            GiftsScreen(
                containers_small=int(m["small"]),
                containers_medium=int(m["medium"]),
                tangerines=int(tangerines["n"]) if tangerines else None,
            )
        ]
    if m := _CONTAINER.match(text):
        size = "small" if m["size"] == "Малый" else "medium"
        return [ContainerOpened(size=size, rewards=_contents(text))]
    if text.startswith(_PRIZEBOX_OPENED):
        money = _MONEY_AFTER.search(text)
        return [
            PrizeboxOpened(
                money_after=num(money["money"]) if money else None, rewards=parse_rewards(text)
            )
        ]
    return []


RECOGNIZERS = (recognize_items,)
