from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import DURATION, NUM, dur, num
from app.engine.types import IncomingMessage

_BUSY = re.compile(r"\A❗️Ты занят другим делом ещё (?P<t>" + DURATION + r")")
_NO_MONEY = re.compile(r"\AТебе не хватает \$(?P<need>" + NUM + r")[\xa0 ]?💵\. Сначала заработай")
_LEFT = re.compile(r"(?:Ещё|Осталось|через) (?P<t>" + DURATION + r")")
_PREFIXES: tuple[tuple[str, str], ...] = (
    ("no_motivation", "Тебе не хватает Мотивации"),
    ("battle_soon", "Скоро Битва, некогда отвлекаться"),
    ("battle_running", "Куда это ты спешишь? Битва уже началась"),
    ("factory_running", "Не торопись. Идёт командная битва за Фабрику"),
    ("tired", "Ты сильно устал"),
    ("tired", "❌Ты сильно устал"),
    ("something_wrong", "Что-то пошло не так"),
    ("card_cooldown", "Ты недавно уже пользовался 💳Подарочной картой"),
    ("not_allowed", "❌Никак нельзя, мой юный падаван"),
    ("fastfood_while_eating", "❌Я всё понимаю, но не стоит питаться фастфудом"),
    ("eat_while_sleeping", "❌Ты собрался поесть, пока спишь?"),
    ("prizebox_locked", "❌Ты пока не подобрал код к призовой коробке"),
    ("no_such_gift", "❌Нельзя просто так взять и открыть несуществующий подарок"),
    ("unknown_command", "Если жаждешь общения, то наш общеигровой чат открыт"),
    ("fastfood_cooldown", "❌Нельзя слишком часто есть фастфуд"),
    ("levelup_required", "Ты достиг нового уровня. Нажми /levelup"),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Busy(Event):
    kind: ClassVar[str] = "busy"
    left_s: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Refused(Event):
    kind: ClassVar[str] = "refused"
    reason: str
    need: int | None = None
    left_s: int | None = None


def recognize_refusals(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if m := _BUSY.match(text):
        return [Busy(left_s=dur(m["t"]))]
    if m := _NO_MONEY.match(text):
        return [Refused(reason="no_money", need=num(m["need"]))]
    for reason, prefix in _PREFIXES:
        if text.startswith(prefix):
            left = _LEFT.search(text)
            return [Refused(reason=reason, left_s=dur(left["t"]) if left else None)]
    return []


RECOGNIZERS = (recognize_refusals,)
