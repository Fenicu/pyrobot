from __future__ import annotations

import logging
from typing import Literal, Protocol

Level = Literal["info", "warn", "error"]
log = logging.getLogger("pyrobot.notify")
_LEVELS = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


class NotifierPort(Protocol):
    async def notify(self, level: Level, code: str, text: str) -> None: ...


class LogNotifier:
    async def notify(self, level: Level, code: str, text: str) -> None:
        log.log(_LEVELS[level], "%s: %s", code, text)
