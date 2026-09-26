from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import Rewards, parse_rewards
from app.engine.types import IncomingMessage

INVITE_CODE = re.compile(r"\Ajoin_fight_[A-Za-z0-9_-]{11}\Z")
_JOINED = re.compile(
    r"\AТы поспешил на помощь (?P<ally>.+?) \((?P<lvl>\d+)\), вам предстоит трудная битва "
    r"против (?P<enemy>🐮Быков|🐻Медведей)"
)
_RESULT = re.compile(r"\AТы с группой друзей вышел сразиться (?P<n>\d) на \d против биржевиков:")
_WON = "Вы успешно побороли"
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
    if m := _RESULT.match(text):
        return [BullsResult(team=int(m["n"]), won=_WON in text, rewards=parse_rewards(text))]
    for reason, prefix in _REFUSALS:
        if text.startswith(prefix):
            return [BullsRefused(reason=reason)]
    return []


RECOGNIZERS = (recognize_bulls,)
INVITE_RECOGNIZERS = (recognize_invite,)
