from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import NUM, Rewards, num, parse_rewards
from app.engine.types import IncomingMessage

_CREW = re.compile(
    r"\AО команде\n.*?^™️Тег: (?P<tag>\S+)$.*?^⚔Побед на фабрике: (?P<wins>\d+)$", re.S | re.M
)
_GLORY = re.compile(r"^🏆Твоя слава: (?P<n>" + NUM + r")$", re.M)
_SIGNUP_OPEN = "❗️Началась запись на ⚔битву за фабрику"
_FACTORY = "⚔Битва за фабрику\n"
_FACTORY_STATUS = (
    ("not_signed", "❗️Ты ещё не записан."),
    ("signed", "👍Ты уже записан!"),
    ("closed", "❗️Жди следующей битвы."),
)
_REPORT = re.compile(
    r"\A[^\n]+ \(\d+\)\n🔨[^\n]*\nБитва за фабрику (?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{2}): "
)
_TREASURY = re.compile(r"^💵[\xa0 ]?В казну: \+\$(?P<v>" + NUM + r")", re.M)
_RAGE = re.compile(r"^😡Твоя Ярость: (?P<v>\d+)", re.M)
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
    # Личная слава «🏆Твоя слава» (не слава команды «🏆Слава»).
    glory: int | None = None


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
    """Личный итог последней битвы за фабрику, в которой был персонаж, — только по запросу (/fb):
    день битвы (ISO-дата из «Битва за фабрику ДД.ММ.ГГ»), награды, взнос в казну команды («💵В
    казну» — не личные деньги) и ярость."""

    kind: ClassVar[str] = "factory_report"
    won: bool
    day: str
    rewards: Rewards = field(default_factory=Rewards)
    treasury: int = 0
    rage: int | None = None

    @property
    def battle_day(self) -> date:
        return date.fromisoformat(self.day)


def recognize_crew(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if m := _CREW.match(text):
        glory = _GLORY.search(text)
        return [
            CrewScreen(
                tag=m["tag"],
                factory_wins=int(m["wins"]),
                signup_open=_SIGNUP_OPEN in text,
                glory=num(glory["n"]) if glory else None,
            )
        ]
    if text.startswith(_FACTORY):
        tail = text.rsplit("\n", 1)[-1]
        status = next((s for s, prefix in _FACTORY_STATUS if tail.startswith(prefix)), None)
        return [FactoryScreen(status=status)] if status else []
    if m := _REPORT.match(text):
        try:
            day = date(2000 + int(m["y"]), int(m["m"]), int(m["d"]))
        except ValueError:
            return []
        treasury = _TREASURY.search(text)
        rage = _RAGE.search(text)
        return [
            FactoryReport(
                won=_REPORT_WON in text,
                day=day.isoformat(),
                rewards=parse_rewards(text),
                treasury=num(treasury["v"]) if treasury else 0,
                rage=int(rage["v"]) if rage else None,
            )
        ]
    for result, prefix in _SIGNUPS:
        if text.startswith(prefix):
            return [FactorySignup(result=result)]
    return []


RECOGNIZERS = (recognize_crew,)
