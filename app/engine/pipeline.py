from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from typing import Any, Protocol

from app.engine.bus import Bus, Delivery
from app.engine.events import Event
from app.engine.parsing import MessageParser
from app.engine.types import IncomingMessage

log = logging.getLogger(__name__)
State = dict[str, Any]


class Reducer(Protocol):
    def apply(self, state: State, msg: IncomingMessage, events: Sequence[Event]) -> State: ...


class NullReducer:
    def apply(self, state: State, msg: IncomingMessage, events: Sequence[Event]) -> State:
        return state


class JournalStore(Protocol):
    async def load_state(self) -> tuple[State, int]: ...

    async def append(
        self,
        msg: IncomingMessage,
        events: Sequence[Event],
        new_state: State | None,
        new_version: int,
        metrics: Mapping[str, float] | None = None,
    ) -> int | None: ...


class Pipeline:
    def __init__(
        self,
        *,
        journal: JournalStore,
        parser: MessageParser,
        reducer: Reducer,
        bus: Bus,
        metrics: Callable[[State, State], Mapping[str, float]] | None = None,
        react_max_age: timedelta = timedelta(minutes=10),
        latest_capacity: int = 5000,
        retry_base_s: float = 0.5,
        retry_max_s: float = 30.0,
    ) -> None:
        self._journal = journal
        self._parser = parser
        self._reducer = reducer
        self._bus = bus
        self._metrics = metrics
        self._react_max_age = react_max_age
        self._queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        self._latest: OrderedDict[tuple[int, int], IncomingMessage] = OrderedDict()
        self._capacity = latest_capacity
        self._retry_base = retry_base_s
        self._retry_max = retry_max_s
        self._state: State = {}
        self._version = 0
        self._last_journal_id = 0
        self._healthy = True
        self._busy = False

    @property
    def state(self) -> State:
        return self._state

    @property
    def version(self) -> int:
        return self._version

    @property
    def last_journal_id(self) -> int:
        return self._last_journal_id

    @property
    def healthy(self) -> bool:
        return self._healthy

    async def load(self) -> None:
        self._state, self._version = await self._journal.load_state()

    def latest(self, chat_id: int, msg_id: int) -> IncomingMessage | None:
        return self._latest.get((chat_id, msg_id))

    async def submit(self, msg: IncomingMessage) -> None:
        await self._queue.put(msg)

    def backlog(self) -> int:
        return self._queue.qsize()

    @property
    def unfinished(self) -> int:
        return self._queue.qsize() + (1 if self._busy else 0)

    async def drain(self, timeout_s: float) -> bool:
        try:
            await asyncio.wait_for(self._queue.join(), timeout_s)
        except TimeoutError:
            return False
        return True

    async def run(self) -> None:
        while True:
            msg = await self._queue.get()
            self._busy = True
            try:
                await self.process(msg)
            except Exception:
                log.exception("pipeline failed on %s/%s", msg.chat_id, msg.msg_id)
            finally:
                self._busy = False
                self._queue.task_done()

    async def process(self, msg: IncomingMessage) -> Delivery | None:
        events = self._parser.parse(msg)
        try:
            new_state = self._reducer.apply(self._state, msg, events)
        except Exception:
            log.exception("reducer failed on %s/%s", msg.chat_id, msg.msg_id)
            new_state = self._state
        changed = new_state != self._state
        version = self._version + 1 if changed else self._version
        metrics = None
        if changed and self._metrics:
            try:
                metrics = self._metrics(self._state, new_state)
            except Exception:
                log.exception("metrics failed on %s/%s", msg.chat_id, msg.msg_id)
                metrics = None
        journal_id = await self._append(
            msg, events, new_state if changed else None, version, metrics
        )
        if journal_id is None:
            return None
        if changed:
            self._state, self._version = new_state, version
        self._last_journal_id = max(self._last_journal_id, journal_id)
        self._remember(msg)
        reactable = not (msg.recovered and msg.received_at - msg.date > self._react_max_age)
        delivery = Delivery(
            msg=msg,
            events=tuple(events),
            state_version=self._version,
            journal_id=journal_id,
            reactable=reactable,
        )
        await self._bus.publish(delivery)
        return delivery

    async def _append(
        self,
        msg: IncomingMessage,
        events: Sequence[Event],
        new_state: State | None,
        version: int,
        metrics: Mapping[str, float] | None,
    ) -> int | None:
        delay = self._retry_base
        while True:
            try:
                journal_id = await self._journal.append(
                    msg, events, new_state, version, metrics=metrics
                )
            except Exception:
                self._healthy = False
                log.exception("journal append failed, retry in %.2fs", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._retry_max)
                continue
            self._healthy = True
            return journal_id

    def _remember(self, msg: IncomingMessage) -> None:
        key = (msg.chat_id, msg.msg_id)
        current = self._latest.get(key)
        if current is not None and msg.revision < current.revision:
            return
        self._latest[key] = msg
        self._latest.move_to_end(key)
        while len(self._latest) > self._capacity:
            self._latest.popitem(last=False)
