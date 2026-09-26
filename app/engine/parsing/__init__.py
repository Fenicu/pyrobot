from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Sequence

from app.engine.events import AntiFlood, Event, Unrecognized
from app.engine.parsing.common import first_line
from app.engine.settings import ChatsSection
from app.engine.types import IncomingMessage

log = logging.getLogger(__name__)

Recognizer = Callable[[IncomingMessage], list[Event]]
ANTIFLOOD_MARK = "Ты шлёшь запросы к боту слишком часто"


def recognize_antiflood(msg: IncomingMessage) -> list[Event]:
    if msg.text and ANTIFLOOD_MARK in msg.text:
        return [AntiFlood()]
    return []


class Parser:
    def __init__(
        self,
        recognizers: Sequence[Recognizer],
        *,
        chats: Collection[int] | None = None,
        report_unrecognized: Collection[int] = (),
    ) -> None:
        self._recognizers = tuple(recognizers)
        self._chats = frozenset(chats) if chats is not None else None
        self._report = frozenset(report_unrecognized)

    def parse(self, msg: IncomingMessage) -> list[Event]:
        if msg.outgoing or not msg.text:
            return []
        if self._chats is not None and msg.chat_id not in self._chats:
            return []
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
        if not events and msg.chat_id in self._report:
            events.append(Unrecognized(first_line=first_line(msg.text)))
        return events


def game_recognizers() -> tuple[Recognizer, ...]:
    return (recognize_antiflood,)


def default_parser(chats: ChatsSection | None = None) -> Parser:
    if chats is None:
        return Parser(game_recognizers())
    return Parser(
        game_recognizers(),
        chats=(chats.game_chat_id,),
        report_unrecognized=(chats.game_chat_id,),
    )
