from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.types import IncomingMessage

_CREW = re.compile(
    r"\AО команде\n.*?^™️Тег: (?P<tag>\S+)$.*?^⚔Побед на фабрике: (?P<wins>\d+)$", re.S | re.M
)
_SIGNUP_OPEN = "❗️Началась запись на ⚔битву за фабрику"
_FACTORY = "⚔Битва за фабрику\n"
_FACTORY_STATUS = (
    ("not_signed", "❗️Ты ещё не записан."),
    ("signed", "👍Ты уже записан!"),
    ("closed", "❗️Жди следующей битвы."),
)
_REPORT = re.compile(r"\A[^\n]+ \(\d+\)\n🔨[^\n]*\nБитва за фабрику \d+\.\d+\.\d+: ")
_REPORT_WON = "принёс победу своей команде"
_SIGNUPS = (
    ("signed", "👍Отлично, ты записался на битву за фабрику!"),
    ("already", "👍Ты уже записался на битву."),
    ("skip", "💪Твоя команда выиграла предыдущую битву. Эту вы пропускаете."),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class CrewScreen(Event):
    kind: ClassVar[str] = "crew_screen"
    tag: str
    factory_wins: int
    signup_open: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class FactoryScreen(Event):
    kind: ClassVar[str] = "factory_screen"
    status: str


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorySignup(Event):
    kind: ClassVar[str] = "factory_signup"
    result: str


@dataclass(frozen=True, slots=True, kw_only=True)
class FactoryReport(Event):
    """Личный итог прошлой битвы за фабрику по запросу (/fb)."""

    kind: ClassVar[str] = "factory_report"
    won: bool


def recognize_crew(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if m := _CREW.match(text):
        return [
            CrewScreen(tag=m["tag"], factory_wins=int(m["wins"]), signup_open=_SIGNUP_OPEN in text)
        ]
    if text.startswith(_FACTORY):
        tail = text.rsplit("\n", 1)[-1]
        status = next((s for s, prefix in _FACTORY_STATUS if tail.startswith(prefix)), None)
        return [FactoryScreen(status=status)] if status else []
    if _REPORT.match(text):
        return [FactoryReport(won=_REPORT_WON in text)]
    for result, prefix in _SIGNUPS:
        if text.startswith(prefix):
            return [FactorySignup(result=result)]
    return []


RECOGNIZERS = (recognize_crew,)
