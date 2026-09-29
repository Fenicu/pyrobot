"""Поток событий для SSE админки: кольцевая история с id `<эпоха>:<номер>` и подписчики с
ограниченными очередями. Публикация синхронная и не ждёт подписчиков: отстающий отключается,
игровой конвейер от него не тормозит."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.engine.bus import Delivery
from app.engine.commands import CommandClass
from app.engine.gateway.store import ActionStore, Closed, Obligation, StoredAction
from app.engine.gateway.types import ActionRequest, ActionStatus
from app.engine.planner.store import DecisionRecord, PlannerStore
from app.engine.planner.types import Decision

HISTORY = 1000
QUEUE_SIZE = 256
# Служебное поле редьюсера в поток не входит (как и в GET /state).
_INTERNAL = frozenset({"applied"})


@dataclass(frozen=True, slots=True)
class StreamEvent:
    seq: int
    type: str
    data: dict[str, Any]


@dataclass(eq=False)
class Subscription:
    queue: asyncio.Queue[StreamEvent]
    replay: list[StreamEvent]
    # new — подключение без Last-Event-ID; epoch — процесс перезапущен; evicted — нужные
    # события вытеснены из истории; unknown — id не разобрать или он из будущего.
    reset: str | None
    # Номер последнего события на момент подписки — id события reset.
    at: int
    dropped: bool = False


class EventStream:
    def __init__(
        self, *, epoch: str | None = None, history: int = HISTORY, queue_size: int = QUEUE_SIZE
    ) -> None:
        self.epoch = epoch or str(int(time.time() * 1000))
        self._seq = 0
        self._history: deque[StreamEvent] = deque(maxlen=history)
        self._queue_size = queue_size
        self._subs: set[Subscription] = set()

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    def event_id(self, seq: int) -> str:
        return f"{self.epoch}:{seq}"

    def history(self) -> list[StreamEvent]:
        return list(self._history)

    def publish(self, type_: str, data: dict[str, Any]) -> None:
        self._seq += 1
        event = StreamEvent(self._seq, type_, data)
        self._history.append(event)
        for sub in list(self._subs):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                sub.dropped = True
                self._subs.discard(sub)

    def subscribe(self, last_event_id: str | None) -> Subscription:
        # Синхронно: между снимком истории и подпиской не проходит ни одна публикация.
        replay, reset = self._replay(last_event_id)
        sub = Subscription(asyncio.Queue(self._queue_size), replay, reset, self._seq)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    def _replay(self, last_event_id: str | None) -> tuple[list[StreamEvent], str | None]:
        if last_event_id is None:
            return [], "new"
        epoch, _, raw = last_event_id.partition(":")
        if not raw.isdigit():
            return [], "unknown"
        if epoch != self.epoch:
            return [], "epoch"
        seq = int(raw)
        if seq > self._seq:
            return [], "unknown"
        oldest = self._history[0].seq if self._history else self._seq + 1
        if seq < oldest - 1:
            return [], "evicted"
        return [e for e in self._history if e.seq > seq], None


def _moment(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class StreamFeed:
    """Подписчик шины: каждое сообщение журнала и изменившиеся поля состояния."""

    def __init__(self, stream: EventStream, state: Callable[[], Mapping[str, Any]]) -> None:
        self._stream = stream
        self._state = state
        self._version = 0
        self._last: dict[str, Any] = {}

    async def on_delivery(self, delivery: Delivery) -> None:
        msg = delivery.msg
        self._stream.publish(
            "message",
            {
                "journal_id": delivery.journal_id,
                "chat_id": msg.chat_id,
                "msg_id": msg.msg_id,
                "revision": msg.revision,
                "kind": msg.kind,
                "date": msg.date.isoformat(),
                "outgoing": msg.outgoing,
                "text": msg.text,
                "events": [e.to_json() for e in delivery.events],
                # Кнопки — как `markup` элемента /journal: по ним админка кликает
                # (`/commands/click` с `chat_id`, `msg_id` и `revision` этой правки).
                "markup": msg.markup_json(),
            },
        )
        if delivery.state_version == self._version:
            return
        current = {k: v for k, v in self._state().items() if k not in _INTERNAL}
        changed = {
            k: current.get(k)
            for k in current.keys() | self._last.keys()
            if current.get(k) != self._last.get(k)
        }
        self._version, self._last = delivery.state_version, current
        self._stream.publish("state", {"version": delivery.state_version, "changed": changed})


@dataclass
class PublishingActionStore:
    """Хранилище действий шлюза, которое сообщает в поток о каждом переходе статуса."""

    inner: ActionStore
    stream: EventStream = field(repr=False)

    async def create(
        self, req: ActionRequest, cls: CommandClass, status: ActionStatus, reason: str = ""
    ) -> int:
        action_id = await self.inner.create(req, cls, status, reason)
        self.stream.publish(
            "action",
            {
                "id": action_id,
                "status": status.value,
                "reason": reason,
                "source": req.source.name.lower(),
                "kind": req.kind.value,
                "chat_id": req.chat_id,
                "text": req.text,
                "data": req.data,
                "command_class": cls.value,
                "scenario_run_id": req.scenario_run_id,
            },
        )
        return action_id

    async def update(
        self,
        action_id: int,
        *,
        status: ActionStatus,
        reason: str = "",
        attempts: int | None = None,
        answer: str | None = None,
        match_detail: str | None = None,
        sent: bool = False,
    ) -> None:
        await self.inner.update(
            action_id,
            status=status,
            reason=reason,
            attempts=attempts,
            answer=answer,
            match_detail=match_detail,
            sent=sent,
        )
        self.stream.publish("action", {"id": action_id, "status": status.value, "reason": reason})

    async def get_by_key(self, key: str) -> StoredAction | None:
        return await self.inner.get_by_key(key)

    async def mark_unfinished_unknown(self) -> list[Closed]:
        return await self.inner.mark_unfinished_unknown()

    async def unreconciled(self) -> list[Obligation]:
        return await self.inner.unreconciled()

    async def mark_reconciled(self, action_ids: Sequence[int]) -> None:
        await self.inner.mark_reconciled(action_ids)


@dataclass
class PublishingPlannerStore:
    """Журнал решений и запусков сценариев, который сообщает в поток о каждой записи."""

    inner: PlannerStore
    stream: EventStream = field(repr=False)

    async def record(self, at: datetime, decision: Decision) -> int:
        decision_id = await self.inner.record(at, decision)
        rec = DecisionRecord.of(decision)
        self.stream.publish(
            "decision",
            {
                "id": decision_id,
                "at": at.isoformat(),
                "kind": rec.kind,
                "scenario": rec.scenario,
                "reason": rec.reason,
                "until": _moment(rec.until),
            },
        )
        return decision_id

    async def run_started(
        self, decision_id: int, scenario: str, params: Mapping[str, Any], at: datetime
    ) -> int:
        run_id = await self.inner.run_started(decision_id, scenario, params, at)
        self._run(run_id, scenario, "running")
        return run_id

    async def run_finished(self, run_id: int, status: str, reason: str, at: datetime) -> None:
        await self.inner.run_finished(run_id, status, reason, at)
        self._run(run_id, None, status, reason)

    async def run_requested(
        self,
        scenario: str,
        params: Mapping[str, Any],
        *,
        requested: Mapping[str, Any],
        key: str,
        by: str,
        at: datetime,
    ) -> tuple[int, bool]:
        run_id, created = await self.inner.run_requested(
            scenario, params, requested=requested, key=key, by=by, at=at
        )
        if created:
            self._run(run_id, scenario, "queued")
        return run_id, created

    async def run_begin(self, run_id: int, at: datetime) -> None:
        await self.inner.run_begin(run_id, at)
        self._run(run_id, None, "running")

    async def close_running(self, at: datetime) -> int:
        return await self.inner.close_running(at)

    async def last_done(self) -> dict[str, datetime]:
        return await self.inner.last_done()

    async def done_on_day(self, day: date) -> dict[str, int]:
        return await self.inner.done_on_day(day)

    def _run(self, run_id: int, scenario: str | None, status: str, reason: str = "") -> None:
        self.stream.publish(
            "scenario_run",
            {"id": run_id, "scenario": scenario, "status": status, "reason": reason},
        )
