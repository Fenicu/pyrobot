from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import NUM, Rewards, num, parse_rewards
from app.engine.parsing.food import FOODS
from app.engine.types import IncomingMessage

# Подарки за 🍊: экран покупки (кнопка «🎁 за 10🍊»), его правки (подтверждение, итог покупки) и
# ответ на /unbox_t.
_SHOP = re.compile(
    r"\AПокупка подарков за 🍊\n\n🎁У тебя: (?P<gifts>" + NUM + r") шт\.\n"
    r".*?^У тебя: (?P<tangerines>" + NUM + r")🍊$",
    re.S | re.M,
)
_BOUGHT = re.compile(
    r"^👍Ты приобрёл (?P<n>" + NUM + r") шт\. подарков\. Заплатил (?P<paid>" + NUM + r")🍊\.$",
    re.M,
)
_CONFIRM = re.compile(
    r"^Ты собираешься купить (?P<n>" + NUM + r") шт\. подарков "
    r"на сумму (?P<cost>" + NUM + r")🍊\. Берёшь\?$",
    re.M,
)
_SHORT = re.compile(r"^❌Тебе не хватает (?P<n>" + NUM + r")🍊 для покупки\.$", re.M)
# Цена подарка за 🍊 (Малый — единственный тип на экране покупки).
TANGERINE_GIFT_PRICE = 10
_OPTION = re.compile(r"g_tangerines_small_(?P<n>\d+)\Z")
_OPENED = "Ты открыл подарок за 🍊."
# Еда в подарке: «🌭 Хот-дог: +2»; 🧀 Сыр и 🍗 Мясо — еда петов, в состоянии её нет.
_FOOD = re.compile(r"^(?P<icon>" + "|".join(FOODS) + r")[\xa0 ]?[^:\n]+: \+(?P<n>\d+)$", re.M)


@dataclass(frozen=True, slots=True, kw_only=True)
class TangerineGiftShop(Event):
    """Экран «Покупка подарков за 🍊» и его правки после покупки: подарков и 🍊 — уже после
    покупки, варианты количества — с кнопок `g_tangerines_small_<N>` (по возрастанию; самая
    большая — сколько хватит 🍊), `short` — сколько 🍊 не хватает на подарок (кнопок тогда нет)."""

    kind: ClassVar[str] = "tangerine_gift_shop"
    gifts: int
    tangerines: int
    options: tuple[int, ...] = ()
    short: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TangerineGiftConfirm(Event):
    """«Ты собираешься купить N шт. подарков на сумму X🍊. Берёшь?» — правка экрана покупки после
    клика по количеству; покупает только `g_tangerines_small_<N>_accept` с этой правки."""

    kind: ClassVar[str] = "tangerine_gift_confirm"
    count: int
    cost: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TangerineGiftBought(Event):
    """«👍Ты приобрёл N шт. подарков. Заплатил X🍊.» в правке экрана покупки: каждая покупка —
    новая правка того же сообщения. Подарки и 🍊 после неё — снимок в `TangerineGiftShop`."""

    kind: ClassVar[str] = "tangerine_gift_bought"
    outcome: ClassVar[bool] = True
    per_revision: ClassVar[bool] = True
    count: int
    paid: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TangerineGiftOpened(Event):
    """Открыт подарок за 🍊 (/unbox_t): 💵, улучшения и фастфуд (`food`: hotdog, pizza, …)."""

    kind: ClassVar[str] = "tangerine_gift_opened"
    outcome: ClassVar[bool] = True
    rewards: Rewards = field(default_factory=Rewards)
    food: dict[str, int] = field(default_factory=dict)


def _options(msg: IncomingMessage) -> tuple[int, ...]:
    found = (_OPTION.match(b.data or "") for b in msg.inline)
    return tuple(sorted(int(m["n"]) for m in found if m is not None))


def _food(text: str) -> dict[str, int]:
    food: dict[str, int] = {}
    for m in _FOOD.finditer(text):
        kind = FOODS[m["icon"]]
        food[kind] = food.get(kind, 0) + int(m["n"])
    return food


def recognize_gifts(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if m := _SHOP.match(text):
        short = _SHORT.search(text)
        shop = TangerineGiftShop(
            gifts=num(m["gifts"]),
            tangerines=num(m["tangerines"]),
            options=_options(msg),
            short=num(short["n"]) if short else None,
        )
        if bought := _BOUGHT.search(text):
            return [shop, TangerineGiftBought(count=num(bought["n"]), paid=num(bought["paid"]))]
        if confirm := _CONFIRM.search(text):
            return [shop, TangerineGiftConfirm(count=num(confirm["n"]), cost=num(confirm["cost"]))]
        return [shop]
    if text.startswith(_OPENED):
        return [TangerineGiftOpened(rewards=parse_rewards(text), food=_food(text))]
    return []


RECOGNIZERS = (recognize_gifts,)
