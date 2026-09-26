from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from app.engine.events import AntiFlood, Event
from app.engine.types import IncomingMessage

log = logging.getLogger(__name__)

Recognizer = Callable[[IncomingMessage], list[Event]]
ANTIFLOOD_MARK = "Ты шлёшь запросы к боту слишком часто"


def recognize_antiflood(msg: IncomingMessage) -> list[Event]:
    if msg.text and ANTIFLOOD_MARK in msg.text:
        return [AntiFlood()]
    return []


class Parser:
    def __init__(self, recognizers: Sequence[Recognizer]) -> None:
        self._recognizers = tuple(recognizers)

    def parse(self, msg: IncomingMessage) -> list[Event]:
        events: list[Event] = []
        for recognize in self._recognizers:
            try:
                events.extend(recognize(msg))
            except Exception:
                log.exception(
                    "recognizer %s failed on %s/%s",
                    getattr(recognize, "__name__", recognize),
                    msg.chat_id,
                    msg.msg_id,
                )
        return events


def default_parser() -> Parser:
    return Parser([recognize_antiflood])
