from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import Rewards, parse_rewards
from app.engine.types import IncomingMessage

_WARNING = re.compile(r"\AТы уже достаточно долго бодрствуешь\. Через (?P<h>\d+) час")
_FORCED = re.compile(
    r"\AТы слишком долго бодрствовал и в итоге свалился спать на целых (?P<h>\d+) час"
)
_MENU = "Все мы рано или поздно нуждаемся во сне"
_MENU_HOTEL = re.compile(r"Сон в отеле - (?P<cost>\d+) 💵")
_MENU_SHORT = re.compile(r"❌Тебе не хватает ещё (?P<need>\d+) 💵")
# Второй шаг — правка меню после выбора часов: место (мост или отель).
_PLACE = re.compile(
    r"\n\nТы проспишь: (?P<h>\d+) час\w*\.\n\nПосле выбора места ты не сможешь отменить сон\. "
    r"Где собираешься спать\?\Z"
)
_HOTEL = re.compile(
    r"\AТы отправился спать красиво в отель на (?P<h>\d+) час\w* за (?P<cost>\d+) 💵"
)
_BRIDGE = re.compile(r"\AТы ушёл спать под мост на (?P<h>\d+) час")
_WAKE_BRIDGE = "Ты отлично выспался"
_WAKE_HOTEL = "Кровать в отеле была не самой лучшей"
_ROBBERY = re.compile(
    r"\AОтлично, ты проснулся и произошла схватка с грабителем (?P<robber>.+?) "
    r"\((?P<lvl>\d+)\)\nТы (?P<res>победил|проиграл) в схватке"
)
_ALERT = re.compile(r"\AОпа, тебя начал грабить (?P<robber>.+?) \((?P<lvl>\d+)\)\. Просыпайся!")
_LOSS = re.compile(
    r"\AТебя ограбил (?P<robber>.+?) \((?P<lvl>\d+)\)\.\s+Ты потерял (?P<pct>\d+)% 💵"
)
_ACKS: tuple[tuple[str, str], ...] = (
    ("hotel_ack", "Ты потратился на отель"),
    ("bridge_ack", "Ты решил не тратиться на отель"),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class SleepWarning(Event):
    kind: ClassVar[str] = "sleep_warning"
    forced_in_s: int


@dataclass(frozen=True, slots=True, kw_only=True)
class FellAsleep(Event):
    kind: ClassVar[str] = "fell_asleep"
    outcome: ClassVar[bool] = True
    where: str
    hours: int
    cost: int = 0
    forced: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class SleepMenu(Event):
    kind: ClassVar[str] = "sleep_menu"
    hotel_cost: int
    short_of: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SleepPlace(Event):
    """Выбор места сна после выбора часов (`sleep_Bridge` / `sleep_Hotel`)."""

    kind: ClassVar[str] = "sleep_place"
    hours: int
    hotel_cost: int


@dataclass(frozen=True, slots=True, kw_only=True)
class WokeUp(Event):
    kind: ClassVar[str] = "woke_up"
    outcome: ClassVar[bool] = True
    where: str
    rewards: Rewards


@dataclass(frozen=True, slots=True, kw_only=True)
class RobberyFight(Event):
    kind: ClassVar[str] = "robbery_fight"
    outcome: ClassVar[bool] = True
    won: bool
    robber: str
    robber_level: int
    rewards: Rewards


@dataclass(frozen=True, slots=True, kw_only=True)
class RobberyAlert(Event):
    """Грабят спящего под мостом: кнопка `rob_awake_<n>` этого сообщения будит, и игра правит его
    на итог драки (`RobberyFight`)."""

    kind: ClassVar[str] = "robbery_alert"
    robber: str
    level: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RobberyLoss(Event):
    """Не проснулся: грабитель забрал `pct`% 💵 — отдельное сообщение через ~4 мин после тревоги,
    сама тревога не правится. Потерю игра пишет строкой «💵Деньги: -$N»."""

    kind: ClassVar[str] = "robbery_loss"
    outcome: ClassVar[bool] = True
    robber: str
    level: int
    pct: int
    rewards: Rewards


@dataclass(frozen=True, slots=True, kw_only=True)
class Acknowledged(Event):
    kind: ClassVar[str] = "acknowledged"
    topic: str


def recognize_sleep(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if m := _WARNING.match(text):
        return [SleepWarning(forced_in_s=int(m["h"]) * 3600)]
    if m := _FORCED.match(text):
        return [FellAsleep(where="bridge", hours=int(m["h"]), forced=True)]
    if text.startswith(_MENU) and (hotel := _MENU_HOTEL.search(text)):
        if place := _PLACE.search(text):
            return [SleepPlace(hours=int(place["h"]), hotel_cost=int(hotel["cost"]))]
        short = _MENU_SHORT.search(text)
        return [
            SleepMenu(
                hotel_cost=int(hotel["cost"]), short_of=int(short["need"]) if short else None
            )
        ]
    if m := _HOTEL.match(text):
        return [FellAsleep(where="hotel", hours=int(m["h"]), cost=int(m["cost"]))]
    if m := _BRIDGE.match(text):
        return [FellAsleep(where="bridge", hours=int(m["h"]))]
    if text.startswith(_WAKE_BRIDGE):
        return [WokeUp(where="bridge", rewards=parse_rewards(text))]
    if text.startswith(_WAKE_HOTEL):
        return [WokeUp(where="hotel", rewards=parse_rewards(text))]
    if m := _ROBBERY.match(text):
        return [
            RobberyFight(
                won=m["res"] == "победил",
                robber=m["robber"],
                robber_level=int(m["lvl"]),
                rewards=parse_rewards(text),
            )
        ]
    if m := _ALERT.match(text):
        return [RobberyAlert(robber=m["robber"], level=int(m["lvl"]))]
    if m := _LOSS.match(text):
        return [
            RobberyLoss(
                robber=m["robber"],
                level=int(m["lvl"]),
                pct=int(m["pct"]),
                rewards=parse_rewards(text),
            )
        ]
    for topic, prefix in _ACKS:
        if text.startswith(prefix):
            return [Acknowledged(topic=topic)]
    return []


RECOGNIZERS = (recognize_sleep,)
