from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import NUM, dur, num, resource
from app.engine.types import IncomingMessage

# Валюта билета → поле состояния с этим ресурсом.
CURRENCIES = {"💵": "money", "📚": "knowledge", "🔩": "raw", "⚙️": "details"}
_KEYS = tuple(CURRENCIES.values())
# До 2023 игра писала ⚙ без VS16.
_CUR = r"(?P<emo>💵|📚|🔩|⚙️?)"
_HEAD = re.compile(r"\AЛотерея - (?P<draw>\d+) тираж\nРозыгрыш через (?P<t>[^\n]+)\n\n")
_BOUGHT = re.compile(r"^" + _CUR + r"За [^:\n]+: (?P<n>\d+) из (?P<limit>\d+)$", re.M)
_PRICE = re.compile(r"^" + _CUR + r"(?P<price>\d+) за шт\.$", re.M)
_HAVE = re.compile(r"^" + _CUR + r"[^:\n]+: \$?(?P<v>" + NUM + r")$", re.M)
_ALL = re.compile(r"^Куплено билетов\nВсего: (?P<total>\d+) шт\.\n", re.M)
_ALL_LINE = re.compile(r"^" + _CUR + r"За [^:\n]+: (?P<n>\d+)$", re.M)
_NONE = "\n\nКуплено 0 билетов.\n"
_OFF = "❌Лотерея пока не проводится"
_CURRENCY = re.compile(
    r"\AПокупка билетов за " + _CUR + r"\nРозыгрыш через (?P<t>[^\n]+)\n\n"
    r"Куплено: (?P<n>\d+) из (?P<limit>\d+)\nЦена: (?P<price>\d+)\S+ за шт\.$",
    re.M,
)
_SHORT = "У тебя не хватает "
_FULL = "Ты уже купил все доступные за "


@dataclass(frozen=True, slots=True, kw_only=True)
class LotteryScreen(Event):
    """Экран тиража (`/tickets`, `🤑Лотерея`): куплено из лимита, цены и ресурсы по валютам."""

    kind: ClassVar[str] = "lottery_screen"
    draw: int
    draw_in_s: int
    bought: dict[str, int]
    limits: dict[str, int]
    prices: dict[str, int]
    resources: dict[str, int]


@dataclass(frozen=True, slots=True, kw_only=True)
class LotteryBought(Event):
    """Ответ «Купить все»: сколько билетов куплено этой командой (не всего за тираж)."""

    kind: ClassVar[str] = "lottery_bought"
    outcome: ClassVar[bool] = True
    draw: int
    draw_in_s: int
    bought: dict[str, int]


@dataclass(frozen=True, slots=True, kw_only=True)
class LotteryCurrency(Event):
    """Экран покупки за одну валюту (`<валюта> => 🤑`, новое сообщение; клик по кнопке
    количества `tickets_<валюта>_<n>` правит его): куплено из лимита, цена; `short` — «не
    хватает», `full` — «уже купил все доступные» (кнопок у обоих нет)."""

    kind: ClassVar[str] = "lottery_currency"
    currency: str
    draw_in_s: int
    bought: int
    limit: int
    price: int
    short: bool = False
    full: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class LotteryOff(Event):
    kind: ClassVar[str] = "lottery_off"


def _currency(emo: str) -> str:
    return CURRENCIES[resource(emo)]


def _all_four(values: dict[str, int]) -> bool:
    return set(values) == set(CURRENCIES.values())


def _screen(draw: int, draw_in_s: int, text: str) -> list[Event]:
    bought = {_currency(m["emo"]): int(m["n"]) for m in _BOUGHT.finditer(text)}
    limits = {_currency(m["emo"]): int(m["limit"]) for m in _BOUGHT.finditer(text)}
    prices = {_currency(m["emo"]): int(m["price"]) for m in _PRICE.finditer(text)}
    have_block = text.split("\nТвои ресурсы\n", 1)
    have = (
        {_currency(m["emo"]): num(m["v"]) for m in _HAVE.finditer(have_block[1])}
        if len(have_block) == 2
        else {}
    )
    if not all(_all_four(v) for v in (bought, prices, have)):
        return []
    return [
        LotteryScreen(
            draw=draw,
            draw_in_s=draw_in_s,
            bought=bought,
            limits=limits,
            prices=prices,
            resources=have,
        )
    ]


def _bought(draw: int, draw_in_s: int, text: str) -> list[Event]:
    if _NONE in text:
        return [LotteryBought(draw=draw, draw_in_s=draw_in_s, bought=dict.fromkeys(_KEYS, 0))]
    head = _ALL.search(text)
    if head is None:
        return []
    bought = {_currency(m["emo"]): int(m["n"]) for m in _ALL_LINE.finditer(text)}
    if not _all_four(bought) or sum(bought.values()) != int(head["total"]):
        return []
    return [LotteryBought(draw=draw, draw_in_s=draw_in_s, bought=bought)]


def recognize_lottery(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if m := _HEAD.match(text):
        draw, draw_in_s = int(m["draw"]), dur(m["t"])
        if _BOUGHT.search(text):
            return _screen(draw, draw_in_s, text)
        return _bought(draw, draw_in_s, text)
    if m := _CURRENCY.match(text):
        return [
            LotteryCurrency(
                currency=_currency(m["emo"]),
                draw_in_s=dur(m["t"]),
                bought=int(m["n"]),
                limit=int(m["limit"]),
                price=int(m["price"]),
                short=_SHORT in text,
                full=_FULL in text,
            )
        ]
    if text.startswith(_OFF):
        return [LotteryOff()]
    return []


RECOGNIZERS = (recognize_lottery,)
