from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import DURATION, dur
from app.engine.types import IncomingMessage

_MENU = re.compile(r"\A🍴Меню\n🔋Выносливость: (?P<st>\d+)%")
_STOCK = re.compile(
    r"(?P<food>🌭|🍕|🍔|🍌)[\xa0 ]?[^\n(]*\(🔋 (?P<lo>\d+)-(?P<hi>\d+)%\): (?P<n>\d+) шт"
)
_COOLDOWN = re.compile(r"^Через (?P<t>" + DURATION + r")$", re.M)
_EATEN = re.compile(
    r"\AТы съел (?P<food>🌭|🍕|🍔|🍌)[\xa0 ]?\S+ и восстановился до (?P<st>\d+)% 🔋\."
    r"(?:\nУдача, ты также получил \+(?P<mot>\d+)🔥)?"
)
FOODS = {"🌭": "hotdog", "🍕": "pizza", "🍔": "burger", "🍌": "banana"}
REQUIRED_FOODS = {"hotdog", "pizza", "burger"}


@dataclass(frozen=True, slots=True, kw_only=True)
class FoodStock:
    count: int
    low: int
    high: int


@dataclass(frozen=True, slots=True, kw_only=True)
class FoodMenu(Event):
    kind: ClassVar[str] = "food_menu"
    stamina: int
    stock: dict[str, FoodStock]
    fastfood_in_s: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class FastfoodEaten(Event):
    kind: ClassVar[str] = "fastfood_eaten"
    outcome: ClassVar[bool] = True
    food: str
    stamina: int
    motivation: int


def recognize_food(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if m := _MENU.match(text):
        stock = {
            FOODS[s["food"]]: FoodStock(count=int(s["n"]), low=int(s["lo"]), high=int(s["hi"]))
            for s in _STOCK.finditer(text)
        }
        # Частичный разбор меню не должен затирать известные запасы.
        if not REQUIRED_FOODS <= stock.keys():
            return []
        cooldown = _COOLDOWN.search(text)
        return [
            FoodMenu(
                stamina=int(m["st"]),
                stock=stock,
                fastfood_in_s=dur(cooldown["t"]) if cooldown else None,
            )
        ]
    if m := _EATEN.match(text):
        return [
            FastfoodEaten(
                food=FOODS[m["food"]], stamina=int(m["st"]), motivation=int(m["mot"] or 0)
            )
        ]
    return []


RECOGNIZERS = (recognize_food,)
