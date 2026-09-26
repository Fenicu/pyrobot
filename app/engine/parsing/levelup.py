from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import SKILL, SKILLS
from app.engine.types import IncomingMessage

_MENU = "Поздравляю с новым уровнем!"
_SKILL = re.compile(r"\AТы увеличил навык (?P<skill>" + SKILL + r")")
_DONE = re.compile(r"За уровень ты получил:\n💵Деньги: \$(?P<money>\d+)")
_MOTIVATION = "На радостях ты восстановил +1🔥"


@dataclass(frozen=True, slots=True, kw_only=True)
class LevelUpStep(Event):
    kind: ClassVar[str] = "levelup_step"
    outcome: ClassVar[bool] = True
    step: str
    skill: str | None = None
    money: int = 0
    motivation: int = 0


def recognize_levelup(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if text.startswith(_MENU):
        return [LevelUpStep(step="menu")]
    if m := _SKILL.match(text):
        skill = SKILLS[m["skill"]]
        if done := _DONE.search(text):
            return [
                LevelUpStep(
                    step="done",
                    skill=skill,
                    money=int(done["money"]),
                    motivation=1 if _MOTIVATION in text else 0,
                )
            ]
        return [LevelUpStep(step="main_skill", skill=skill)]
    return []


RECOGNIZERS = (recognize_levelup,)
