import asyncio
from collections.abc import AsyncIterator

from app.engine.bus import Delivery
from app.engine.clock import Clock, SystemClock
from app.engine.events import Event
from app.engine.gateway.gateway import ActionGateway
from app.engine.gateway.types import (
    ActionKind,
    ActionRequest,
    Expectation,
    Match,
    Verdict,
)
from app.engine.memory import MemoryActionStore
from app.engine.settings import EngineSection, Settings, StaticSettings
from app.engine.transport.fake import FakeTransport, Sent
from app.engine.types import IncomingMessage
from tests.engine.helpers import GAME, make_msg

LIVE = Settings(
    engine=EngineSection(
        mode="live",
        min_request_interval_s=0.0,
        antiflood_pause_s=0.0,
        default_expect_timeout_s=0.3,
        click_answer_timeout_s=0.05,
    )
)


class Rig:
    def __init__(self, settings: Settings = LIVE, clock: Clock | None = None) -> None:
        self.transport = FakeTransport()
        self.store = MemoryActionStore()
        self.settings = StaticSettings(settings.model_copy(deep=True))
        self.latest: dict[tuple[int, int], IncomingMessage] = {}
        self.jid = 0
        self.block: str | None = None
        self.version = 0
        self.gw = ActionGateway(
            transport=self.transport,
            store=self.store,
            settings=self.settings,
            latest=lambda c, m: self.latest.get((c, m)),
            boundary=lambda: self.jid,
            clock=clock or SystemClock(),
            can_send=lambda: self.block,
            state_version=lambda: self.version,
        )
        self.task: asyncio.Task[None] | None = None

    async def deliver(
        self, msg: IncomingMessage, events: tuple[Event, ...] = (), journal_id: int | None = None
    ) -> None:
        if journal_id is None:
            self.jid += 1
            journal_id = self.jid
        await self.gw.on_delivery(Delivery(msg, events, 0, journal_id, True))

    def reply_with(self, text: str) -> None:
        async def responder(rec: Sent) -> None:
            await self.deliver(make_msg(text, msg_id=900))

        self.transport.responder = responder

    def start(self) -> None:
        self.task = asyncio.create_task(self.gw.run())

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


async def running_rig(settings: Settings = LIVE) -> AsyncIterator[Rig]:
    rig = Rig(settings)
    rig.start()
    try:
        yield rig
    finally:
        await rig.stop()


def expect_text(fragment: str, refuse: str | None = None, timeout: float = 0.3) -> Expectation:
    def predicate(d: Delivery) -> Match | None:
        text = d.msg.text or ""
        if refuse and refuse in text:
            return Match(Verdict.REFUSED, refuse)
        if fragment in text:
            return Match(Verdict.CONFIRMED, fragment)
        return None

    return Expectation(predicate, timeout)


def send(text: str, **kw: object) -> ActionRequest:
    return ActionRequest(kind=ActionKind.SEND, chat_id=GAME, text=text, **kw)  # type: ignore[arg-type]
