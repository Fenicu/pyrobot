from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import DURATION, dur
from app.engine.types import IncomingMessage

_NOT_PLAYER = re.compile(r"\A❌Увы, (?P<target>.+?) пока не играет в StartupWars")
_COOLDOWN = re.compile(
    r"\A❌А ты знаешь, что мандаринки нельзя разбрасывать так часто\?.*?"
    r"Сможешь отправить новые через (?P<t>" + DURATION + r")",
    re.S,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class TangerineRefused(Event):
    kind: ClassVar[str] = "tangerine_refused"
    reason: str
    target: str | None = None
    left_s: int | None = None


def recognize_tangerine(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if m := _NOT_PLAYER.match(text):
        return [TangerineRefused(reason="not_player", target=m["target"])]
    if m := _COOLDOWN.match(text):
        return [TangerineRefused(reason="cooldown", left_s=dur(m["t"]))]
    return []


RECOGNIZERS = (recognize_tangerine,)
