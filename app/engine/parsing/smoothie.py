from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

from app.engine.events import Event
from app.engine.types import IncomingMessage

# Порядок ингредиентов на экране совпадает с sm_drop_1..5.
INGREDIENTS = {"🍋": "lemon", "🍇": "grape", "🍏": "apple", "🥕": "carrot", "🍅": "tomato"}
_FRUIT = "[" + "".join(INGREDIENTS) + "]"
_SCREEN = "🍹Смузийная\n"
_STOCK = re.compile(r"^(?P<emo>" + _FRUIT + r")\w+ - (?P<n>\d+) шт\.$", re.M)
_CURRENT = re.compile(r"\nТекущий бонус\n(?P<bonus>[^\n]+)")
_COOKING = re.compile(
    r"\A🍹Готовлю\n\n(?:Вброшено\n(?:Пока ничего|(?P<dropped>" + _FRUIT + r"{1,5}))"
    r"|(?P<cancelled>Приготовление отменено)[^\n]*)\n\nУ тебя\n"
)
_COOKED = re.compile(r"\AТы приготовил 🍹Смузи\n(?P<recipe>" + _FRUIT + r"{5})\n")
_GOT = re.compile(r"\nПолучен бонус\n(?P<bonus>[^\n]+)")
_RECIPE = re.compile(
    r"\AРецепт: (?P<recipe>" + _FRUIT + r"{5})\n\nПриготовил: [^\n]+\n\nБонус: (?P<bonus>[^\n]+)"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class SmoothieScreen(Event):
    kind: ClassVar[str] = "smoothie_screen"
    ingredients: dict[str, int] = field(default_factory=dict)
    bonus: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SmoothieCooking(Event):
    """Экран варки: что уже вброшено. Остатки на нём уже без вброшенного — в состояние не идут."""

    kind: ClassVar[str] = "smoothie_cooking"
    dropped: str = ""
    cancelled: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class SmoothieCooked(Event):
    kind: ClassVar[str] = "smoothie_cooked"
    outcome: ClassVar[bool] = True
    recipe: str
    bonus: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SmoothieRecipe(Event):
    kind: ClassVar[str] = "smoothie_recipe"
    recipe: str
    bonus: str


def recognize_smoothie(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if text.startswith(_SCREEN):
        ingredients = {INGREDIENTS[m["emo"]]: int(m["n"]) for m in _STOCK.finditer(text)}
        # Экран только целиком: частично разобранные остатки не должны затирать известные.
        if len(ingredients) != len(INGREDIENTS):
            return []
        current = _CURRENT.search(text)
        return [
            SmoothieScreen(ingredients=ingredients, bonus=current["bonus"] if current else None)
        ]
    if m := _COOKING.match(text):
        return [SmoothieCooking(dropped=m["dropped"] or "", cancelled=bool(m["cancelled"]))]
    if m := _COOKED.match(text):
        got = _GOT.search(text)
        return [SmoothieCooked(recipe=m["recipe"], bonus=got["bonus"] if got else None)]
    return []


def recognize_recipe(msg: IncomingMessage) -> list[Event]:
    if m := _RECIPE.match(msg.text or ""):
        return [SmoothieRecipe(recipe=m["recipe"], bonus=m["bonus"])]
    return []


RECOGNIZERS = (recognize_smoothie,)
CHANNEL_RECOGNIZERS = (recognize_recipe,)
