from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import DURATION, Rewards, dur, parse_rewards
from app.engine.types import IncomingMessage

INVITE_CODE = re.compile(r"\Ajoin_fight_[A-Za-z0-9_-]{11}\Z")
_JOINED = re.compile(
    r"\AТы поспешил на помощь (?P<ally>.+?) \((?P<lvl>\d+)\), вам предстоит трудная битва "
    r"против (?P<enemy>🐮Быков|🐻Медведей)"
)
_RESULT = re.compile(r"\AТы с группой друзей вышел сразиться (?P<n>\d) на \d против биржевиков:")
_WON = "Вы успешно побороли"
# Встреча на ночной прогулке: кнопки fight_accept / fight_decline, срок на раздумья.
_ENCOUNTER = re.compile(
    r"\A(?:Гуляя|Прогуливаясь)[^\n]*?ты заметил[^\n]*?(?P<enemy>🐮Быка|🐻Медведя)"
)
_ENCOUNTER_LEFT = re.compile(r"^У тебя есть (?P<t>" + DURATION + r") ?на раздумья\.$", re.M)
_ENEMIES = {"🐮Быка": "bull", "🐻Медведя": "bear"}
_REFUSALS = (
    ("already_won", "Ты уже побеждал биржевиков этой ночью"),
    ("ended", "Битва уже закончилась, ты не успел присоединиться"),
    ("missing", "Такой битвы нет."),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class BullsInvite(Event):
    kind: ClassVar[str] = "bulls_invite"
    code: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BullsJoined(Event):
    kind: ClassVar[str] = "bulls_joined"
    ally: str
    enemy: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BullsResult(Event):
    kind: ClassVar[str] = "bulls_result"
    outcome: ClassVar[bool] = True
    team: int
    won: bool
    rewards: Rewards


@dataclass(frozen=True, slots=True, kw_only=True)
class BullsEncounter(Event):
    """Предложение подраться с биржевиком на прогулке (`bull`, `bear`) и срок на раздумья. Бот на
    него не отвечает: предложение истекает само."""

    kind: ClassVar[str] = "bulls_encounter"
    enemy: str
    expires_in_s: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class BullsRefused(Event):
    kind: ClassVar[str] = "bulls_refused"
    reason: str


def recognize_invite(msg: IncomingMessage) -> list[Event]:
    for button in msg.inline:
        for code in (button.switch, button.data):
            if code and INVITE_CODE.match(code):
                return [BullsInvite(code=code)]
    return []


def recognize_bulls(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if m := _JOINED.match(text):
        return [BullsJoined(ally=m["ally"], enemy=m["enemy"])]
    if m := _ENCOUNTER.match(text):
        left = _ENCOUNTER_LEFT.search(text)
        return [
            BullsEncounter(
                enemy=_ENEMIES[m["enemy"]], expires_in_s=dur(left["t"]) if left else None
            )
        ]
    if m := _RESULT.match(text):
        return [BullsResult(team=int(m["n"]), won=_WON in text, rewards=parse_rewards(text))]
    for reason, prefix in _REFUSALS:
        if text.startswith(prefix):
            return [BullsRefused(reason=reason)]
    return []


RECOGNIZERS = (recognize_bulls,)
INVITE_RECOGNIZERS = (recognize_invite,)
