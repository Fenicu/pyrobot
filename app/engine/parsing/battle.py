from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import COMPANIES, DURATION, dur
from app.engine.types import IncomingMessage

_TARGET = re.compile(
    r"\AТы решил взломать (?P<target>.+?)\. Следующая Битва через (?P<t>" + DURATION + r")!"
)
_DEFENSE = re.compile(
    r"\AТы встал на защиту 🛡 от налётчиков\. До боя осталось (?P<t>" + DURATION + r")"
)
_ZERO_STAMINA = "Твоя 🔋Выносливость на нуле"
DEFENSE = "🛡Защита"


@dataclass(frozen=True, slots=True, kw_only=True)
class BattleTargetSet(Event):
    kind: ClassVar[str] = "battle_target_set"
    target: str
    battle_in_s: int
    zero_stamina: bool


def _company(name: str) -> str:
    # Точка в конце названия («⚡️Stark Ind.») может слиться с точкой предложения.
    return name + "." if name + "." in COMPANIES else name


def recognize_battle_target(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    zero = _ZERO_STAMINA in text
    if m := _TARGET.match(text):
        target = _company(m["target"])
        return [BattleTargetSet(target=target, battle_in_s=dur(m["t"]), zero_stamina=zero)]
    if m := _DEFENSE.match(text):
        return [BattleTargetSet(target=DEFENSE, battle_in_s=dur(m["t"]), zero_stamina=zero)]
    return []


RECOGNIZERS = (recognize_battle_target,)
