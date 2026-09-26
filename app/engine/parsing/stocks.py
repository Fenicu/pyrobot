from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import COMPANIES, COMPANY, NUM, num
from app.engine.types import IncomingMessage

_SCREENS = (
    ("main", "Акции - /help_stock\n"),
    ("buy", "Покупка акций\n"),
    ("sell", "Продажа акций\n"),
)
_QUOTE = re.compile(r"^(?P<co>" + COMPANY + r") - (?P<price>\d+) 💵 за шт\.$", re.M)
_HOLDING = re.compile(
    r"^(?P<co>" + COMPANY + r") - (?P<qty>" + NUM + r") шт\. на \$" + NUM + r" 💵$", re.M
)
_MONEY = re.compile(r"^💵Деньги: \$(?P<money>" + NUM + r")$", re.M)
_HOURS = re.compile(
    r"⏱Биржа работает с (?P<open>\d+) до (?P<close>\d+) по Москве\.(?: (?P<closed>Закрыта)\.)?"
)
_MIN_BUY = re.compile(r"Акции дешевле (?P<n>\d+) 💵 нельзя купить")
_MAX_SELL = re.compile(r"Акции дороже (?P<n>\d+) 💵 нельзя продать")
_RESERVE = re.compile(r"не менее (?P<n>\d+) 💵 после покупки")
_BOUGHT = re.compile(
    r"\AПокупаем акции (?P<co>" + COMPANY + r")\nЦена (?P<price>\d+) 💵 за шт\.\n\n"
    r"💵Деньги: \$(?P<money>" + NUM + r")\n📈Акции: (?P<shares>" + NUM + r")\n\n"
    r"Куплено акций (?:" + COMPANY + r"): (?P<n>\d+)"
)
_DIVIDENDS = re.compile(
    r"\AТы получаешь дивиденды на все акции своей компании в размере: \$(?P<amount>" + NUM + r")"
)
_QUOTES_HEADER = "Текущие котировки"
_HOLDINGS_HEADER = "Акции у тебя"


@dataclass(frozen=True, slots=True, kw_only=True)
class StockScreen(Event):
    kind: ClassVar[str] = "stock_screen"
    screen: str
    quotes: dict[str, int] = field(default_factory=dict)
    holdings: dict[str, int] = field(default_factory=dict)
    money: int | None = None
    open_hour: int | None = None
    close_hour: int | None = None
    closed: bool = False
    min_buy: int | None = None
    max_sell: int | None = None
    reserve: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StockBought(Event):
    kind: ClassVar[str] = "stock_bought"
    company: str
    price: int
    n: int
    money: int
    shares: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Dividends(Event):
    kind: ClassVar[str] = "dividends"
    outcome: ClassVar[bool] = True
    amount: int


def _optional(pattern: re.Pattern[str], text: str) -> int | None:
    m = pattern.search(text)
    return int(m["n"]) if m else None


def _holdings_valid(text: str) -> bool:
    # Нераспознанная строка портфеля молча потеряла бы позицию в снимке.
    for block in text.split("\n\n"):
        if block.startswith(_HOLDINGS_HEADER):
            lines = [line for line in block.split("\n")[1:] if line]
            return all(_HOLDING.match(line) for line in lines)
    return True


def _screen(screen: str, text: str) -> StockScreen | None:
    hours = _HOURS.search(text)
    money = _MONEY.search(text)
    quotes = {COMPANIES[m["co"]]: int(m["price"]) for m in _QUOTE.finditer(text)}
    # Экран только целиком: если котировки заявлены, должны быть разобраны все компании и 💵.
    if _QUOTES_HEADER in text and (len(quotes) != len(COMPANIES) or money is None):
        return None
    if not _holdings_valid(text):
        return None
    return StockScreen(
        screen=screen,
        quotes=quotes,
        holdings={COMPANIES[m["co"]]: num(m["qty"]) for m in _HOLDING.finditer(text)},
        money=num(money["money"]) if money else None,
        open_hour=int(hours["open"]) if hours else None,
        close_hour=int(hours["close"]) if hours else None,
        closed=bool(hours and hours["closed"]),
        min_buy=_optional(_MIN_BUY, text),
        max_sell=_optional(_MAX_SELL, text),
        reserve=_optional(_RESERVE, text),
    )


def recognize_stocks(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    for screen, prefix in _SCREENS:
        if text.startswith(prefix):
            parsed = _screen(screen, text)
            return [parsed] if parsed is not None else []
    if m := _BOUGHT.match(text):
        return [
            StockBought(
                company=COMPANIES[m["co"]],
                price=int(m["price"]),
                n=int(m["n"]),
                money=num(m["money"]),
                shares=num(m["shares"]),
            )
        ]
    if m := _DIVIDENDS.match(text):
        return [Dividends(amount=num(m["amount"]))]
    return []


RECOGNIZERS = (recognize_stocks,)
