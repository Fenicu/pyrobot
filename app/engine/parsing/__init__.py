from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.engine.events import AntiFlood, Event, Unrecognized
from app.engine.parsing import (
    activities,
    artifacts,
    battle,
    bulls,
    crew,
    daily,
    food,
    gorbushka,
    items,
    levelup,
    lottery,
    metro,
    profile,
    refusals,
    screens,
    sleep,
    smoothie,
    stocks,
    swinfo,
    tangerine,
)
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


class MessageParser(Protocol):
    def parse(self, msg: IncomingMessage) -> list[Event]: ...


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


@dataclass(frozen=True, slots=True)
class Route:
    parser: Parser
    sender: int | None = None


class ChatRouter:
    """Разбор по чату: у чата свои маршруты; маршрут с отправителем — только для его сообщений."""

    def __init__(self, routes: Mapping[int, Sequence[Route]]) -> None:
        self._routes = {chat: tuple(r) for chat, r in routes.items()}

    def parse(self, msg: IncomingMessage) -> list[Event]:
        events: list[Event] = []
        for route in self._routes.get(msg.chat_id, ()):
            if route.sender is None or msg.from_id == route.sender:
                events.extend(route.parser.parse(msg))
        return events


def game_recognizers() -> tuple[Recognizer, ...]:
    return (
        recognize_antiflood,
        *profile.RECOGNIZERS,
        *battle.RECOGNIZERS,
        *activities.RECOGNIZERS,
        *refusals.RECOGNIZERS,
        *sleep.RECOGNIZERS,
        *food.RECOGNIZERS,
        *items.RECOGNIZERS,
        *gorbushka.RECOGNIZERS,
        *levelup.RECOGNIZERS,
        *crew.RECOGNIZERS,
        *daily.RECOGNIZERS,
        *bulls.RECOGNIZERS,
        *stocks.RECOGNIZERS,
        *smoothie.RECOGNIZERS,
        *tangerine.RECOGNIZERS,
        *metro.RECOGNIZERS,
        *lottery.RECOGNIZERS,
        *artifacts.RECOGNIZERS,
        *screens.RECOGNIZERS,
    )


def default_parser(chats: ChatsSection | None = None) -> MessageParser:
    if chats is None:
        return Parser(game_recognizers())
    routes: dict[int, list[Route]] = {}
    game = Parser(game_recognizers(), report_unrecognized=(chats.game_chat_id,))
    routes.setdefault(chats.game_chat_id, []).append(Route(game))
    swinfo_route = Route(Parser(swinfo.RECOGNIZERS), sender=chats.swinfo_user_id)
    routes.setdefault(chats.swinfo_chat_id, []).append(swinfo_route)
    if chats.smoothie_channel_id is not None:
        channel = Route(Parser(smoothie.CHANNEL_RECOGNIZERS))
        routes.setdefault(chats.smoothie_channel_id, []).append(channel)
    if chats.bulls_invite_chat_id is not None:
        invites = Route(Parser(bulls.INVITE_RECOGNIZERS))
        routes.setdefault(chats.bulls_invite_chat_id, []).append(invites)
    return ChatRouter(routes)
