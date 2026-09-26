from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import DURATION, NUM, dur, num
from app.engine.types import IncomingMessage

_INVENTORY = "Гаджеты при тебе: (снять)"
_BOOKS = re.compile(r"^📒Книга опыта: (?P<n>\d+)", re.M)
_CARDS = re.compile(r"^💳Подарочная карта: (?P<n>\d+)", re.M)
_PRIZEBOX = re.compile(
    r"^🎁[\xa0 ]?Призовая коробка /unbox(?: \((?P<t>" + DURATION + r")\))?$", re.M
)
_SLOTS = re.compile(r"Занято (?P<used>\d+) из (?P<cap>\d+)")
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
_PRIZEBOX_OPENED = "👍Ура! Тебе удалось наконец-то открыть призовую коробку."
_MONEY_AFTER = re.compile(r"Стало: \$(?P<money>" + NUM + r")")


@dataclass(frozen=True, slots=True, kw_only=True)
class Inventory(Event):
    kind: ClassVar[str] = "inventory"
    books: int
    cards: int
    prizebox: bool
    prizebox_in_s: int | None
    bag: int
    bag_cap: int


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
    kind: ClassVar[str] = "container_opened"
    outcome: ClassVar[bool] = True
    size: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PrizeboxOpened(Event):
    kind: ClassVar[str] = "prizebox_opened"
    outcome: ClassVar[bool] = True
    money_after: int | None


def _inventory(text: str) -> list[Event]:
    slots, books, cards = _SLOTS.search(text), _BOOKS.search(text), _CARDS.search(text)
    # Без строк книг и карт экран считается неразобранным, а не «ноль книг».
    if slots is None or books is None or cards is None:
        return []
    box = _PRIZEBOX.search(text)
    return [
        Inventory(
            books=int(books["n"]),
            cards=int(cards["n"]),
            prizebox=box is not None,
            prizebox_in_s=dur(box["t"]) if box and box["t"] else None,
            bag=int(slots["used"]),
            bag_cap=int(slots["cap"]),
        )
    ]


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
        return [ContainerOpened(size="small" if m["size"] == "Малый" else "medium")]
    if text.startswith(_PRIZEBOX_OPENED):
        money = _MONEY_AFTER.search(text)
        return [PrizeboxOpened(money_after=num(money["money"]) if money else None)]
    return []


RECOGNIZERS = (recognize_items,)
