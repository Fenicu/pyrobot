"""Эмулятор игры для сертификации сценариев: отвечает реальными сообщениями из фикстур."""

import asyncio
import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from app.engine.bus import Bus
from app.engine.clock import SystemClock
from app.engine.gateway.gateway import ActionGateway
from app.engine.memory import MemoryActionStore, MemoryJournal
from app.engine.parsing import default_parser
from app.engine.pipeline import Pipeline
from app.engine.settings import ChatsSection, EngineSection, Settings, StaticSettings
from app.engine.state.model import CharacterState, load_state
from app.engine.state.reducer import StateReducer
from app.engine.types import IncomingMessage
from tests.fixtures import game_msg

GAME = 227859379
Ref = tuple[str, int]


@dataclass
class Reply:
    new: list[Ref] = field(default_factory=list)
    edit: Ref | None = None


@dataclass(frozen=True)
class Sent:
    kind: str
    payload: str
    message_id: int | None
    chat_id: int | None = None
    reply_to: int | None = None


class FakeGame:
    """Transport: команда или клик → ответ реальными сообщениями, через конвейер."""

    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline
        self._text: dict[str, list[Reply]] = {}
        self._click: dict[str, list[Reply]] = {}
        self._ids = itertools.count(9_000_000)
        self._revisions = itertools.count(1)
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self.sent: list[Sent] = []
        self.messages: dict[int, IncomingMessage] = {}

    def on_text(self, text: str, *new: Ref) -> None:
        self._text.setdefault(text, []).append(Reply(new=list(new)))

    def on_click(self, data: str, *, edit: Ref | None = None, new: tuple[Ref, ...] = ()) -> None:
        self._click.setdefault(data, []).append(Reply(new=list(new), edit=edit))

    def payloads(self) -> list[str]:
        return [s.payload for s in self.sent]

    async def settle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks))

    async def send_text(self, chat_id: int, text: str, reply_to: int | None = None) -> int:
        self.sent.append(Sent("send", text, None, chat_id, reply_to))
        self._schedule(self._text, text, None)
        return next(self._ids)

    async def click(
        self, chat_id: int, message_id: int, data: str, timeout_s: float
    ) -> str | None:
        self.sent.append(Sent("click", data, message_id))
        self._schedule(self._click, data, message_id)
        return None

    def _schedule(self, table: dict[str, list[Reply]], key: str, message_id: int | None) -> None:
        replies = table.get(key)
        if not replies:
            return
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        task = asyncio.ensure_future(self._deliver(reply, message_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, reply: Reply, message_id: int | None) -> None:
        await asyncio.sleep(0)
        async with self._lock:
            now = datetime.now(UTC)
            if reply.edit is not None and message_id is not None:
                original = self.messages[message_id]
                msg = replace(
                    game_msg(*reply.edit),
                    msg_id=message_id,
                    kind="edit",
                    revision=next(self._revisions),
                    date=now,
                    received_at=now,
                    created_at=original.origin,
                )
                await self._push(msg)
            for ref in reply.new:
                msg = replace(
                    game_msg(*ref),
                    msg_id=next(self._ids),
                    kind="new",
                    revision=0,
                    date=now,
                    received_at=now,
                    created_at=now,
                )
                await self._push(msg)

    async def _push(self, msg: IncomingMessage) -> None:
        self.messages[msg.msg_id] = msg
        await self._pipeline.process(msg)


LIVE = Settings(
    engine=EngineSection(
        mode="live",
        min_request_interval_s=0.0,
        antiflood_pause_s=0.0,
        default_expect_timeout_s=0.3,
        click_answer_timeout_s=0.05,
    )
)


class World:
    def __init__(self, settings: Settings = LIVE) -> None:
        self.settings = StaticSettings(settings.model_copy(deep=True))
        self.bus = Bus()
        self.store = MemoryActionStore()
        self.pipeline = Pipeline(
            journal=MemoryJournal(),
            parser=default_parser(ChatsSection()),
            reducer=StateReducer(),
            bus=self.bus,
        )
        self.game = FakeGame(self.pipeline)
        self.gateway = ActionGateway(
            transport=self.game,
            store=self.store,
            settings=self.settings,
            latest=self.pipeline.latest,
            boundary=lambda: self.pipeline.last_journal_id,
            clock=SystemClock(),
        )
        self.bus.subscribe(self.gateway.on_delivery, priority=0)
        self._task: asyncio.Task[None] | None = None

    @property
    def state(self) -> CharacterState:
        return load_state(self.pipeline.state)

    async def feed(self, family: str, msg_id: int) -> None:
        now = datetime.now(UTC)
        await self.pipeline.process(
            replace(
                game_msg(family, msg_id), msg_id=msg_id, date=now, received_at=now, created_at=now
            )
        )

    def start(self) -> None:
        self._task = asyncio.create_task(self.gateway.run())

    async def stop(self) -> None:
        await self.game.settle()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


async def running_world(settings: Settings = LIVE) -> AsyncIterator[World]:
    world = World(settings)
    world.start()
    try:
        yield world
    finally:
        await world.stop()
