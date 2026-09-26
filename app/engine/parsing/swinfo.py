from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import COMPANIES, COMPANY, NUM, num
from app.engine.types import IncomingMessage

_FACTORY_CALL = (
    "Внимание 👫командных игроков!\nВ ваших командах началась запись на битву за Фабрику"
)
_FACTORY_RESULT = "Битва за контроль над Фабрикой"
# «🏅Победила команда ☣️ST (1)»: эмодзи компании, затем тег команды.
_FACTORY_WINNER = re.compile(r"^🏅Победила команда (?:📯|🤖|⚡️|☂️|🎩|☣️)(?P<tag>\S+) \(", re.M)
_BATTLE_HEAD = re.compile(r"\A(?:🛡|⚔)[^\n]*(?P<co>" + COMPANY + r")")
_BLOCK_COMPANY = re.compile(r"(?P<co>" + COMPANY + r")")
_PRICE = re.compile(r"^(?:📈|📉)Акции компании[^\n$]*\$(?P<price>" + NUM + r")", re.M)
_LOTTERY = (
    ("start", re.compile(r"\AСтартовал (?P<draw>\d+) тираж SW-лотереи!")),
    ("prizes", re.compile(r"\A🤑Призов в (?P<draw>\d+) тираже на данный момент")),
    ("end", re.compile(r"\AВот и завершился (?P<draw>\d+) тираж SW-лотереи\.")),
)
_DAY_RATING = "Рейтинг компаний за день\n"


@dataclass(frozen=True, slots=True, kw_only=True)
class FactoryCall(Event):
    kind: ClassVar[str] = "factory_call"


@dataclass(frozen=True, slots=True, kw_only=True)
class FactoryResult(Event):
    kind: ClassVar[str] = "factory_result"
    winner: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class BattleSummary(Event):
    kind: ClassVar[str] = "battle_summary"
    prices: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class LotteryPost(Event):
    kind: ClassVar[str] = "lottery_post"
    stage: str
    draw: int


@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyDayRating(Event):
    kind: ClassVar[str] = "company_day_rating"


def _prices(text: str) -> dict[str, int]:
    prices: dict[str, int] = {}
    for block in text.split("\n\n"):
        company = _BLOCK_COMPANY.search(block.split("\n", 1)[0])
        price = _PRICE.search(block)
        if company and price:
            prices[COMPANIES[company["co"]]] = num(price["price"])
    return prices


def recognize_swinfo(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if text.startswith(_FACTORY_CALL):
        return [FactoryCall()]
    if text.startswith(_FACTORY_RESULT):
        winner = _FACTORY_WINNER.search(text)
        return [FactoryResult(winner=winner["tag"] if winner else None)]
    if _BATTLE_HEAD.match(text):
        return [BattleSummary(prices=_prices(text))]
    for stage, pattern in _LOTTERY:
        if m := pattern.match(text):
            return [LotteryPost(stage=stage, draw=int(m["draw"]))]
    if text.startswith(_DAY_RATING):
        return [CompanyDayRating()]
    return []


RECOGNIZERS = (recognize_swinfo,)
