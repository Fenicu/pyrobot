from __future__ import annotations

import asyncio
import itertools
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from app.engine.bus import Delivery
from app.engine.clock import Clock
from app.engine.commands import CommandClass, classify_callback, classify_text
from app.engine.events import AntiFlood
from app.engine.gateway.store import ActionStore, DuplicateKey
from app.engine.gateway.types import (
    ActionKind,
    ActionRequest,
    ActionResult,
    ActionStatus,
    Match,
    Source,
    Verdict,
)
from app.engine.settings import SettingsProvider
from app.engine.transport.base import (
    FloodWait,
    Transport,
    TransportAuthLost,
    TransportRejected,
)
from app.engine.types import IncomingMessage

log = logging.getLogger(__name__)

LatestLookup = Callable[[int, int], IncomingMessage | None]
Boundary = Callable[[], int]
Blocked = tuple[ActionStatus, str]
DATE_SKEW = timedelta(seconds=2)
MAX_FLOODWAIT_S = 300.0


@dataclass(eq=False)
class _Pending:
    req: ActionRequest
    cls: CommandClass
    seq: int
    enqueued: float
    ttl: float
    future: asyncio.Future[ActionResult]


@dataclass(eq=False)
class _InFlight:
    req: ActionRequest
    sent_at: datetime
    boundary: int
    matched: asyncio.Future[Match]
    antiflood: asyncio.Event = field(default_factory=asyncio.Event)


class Lease:
    def __init__(self, owner: str) -> None:
        self.token = uuid.uuid4().hex
        self.owner = owner
        self.safe = False


def command_class(req: ActionRequest) -> CommandClass:
    if req.kind is ActionKind.SEND:
        return classify_text(req.text or "")
    return classify_callback(req.data or "")


class ActionGateway:
    def __init__(
        self,
        *,
        transport: Transport,
        store: ActionStore,
        settings: SettingsProvider,
        latest: LatestLookup,
        boundary: Boundary,
        clock: Clock,
    ) -> None:
        self._transport = transport
        self._store = store
        self._settings = settings
        self._latest = latest
        self._boundary = boundary
        self._clock = clock
        self._queue: list[_Pending] = []
        self._cond = asyncio.Condition()
        self._seq = itertools.count()
        self._inflight: _InFlight | None = None
        self._lease: Lease | None = None
        self._last_send = 0.0
        self._kill_reason: str | None = None
        self._spend_block: str | None = None
        self._keys: dict[str, asyncio.Future[ActionResult]] = {}
        self._closed = False

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    @property
    def in_flight(self) -> ActionRequest | None:
        return self._inflight.req if self._inflight else None

    @property
    def lease(self) -> Lease | None:
        return self._lease

    @property
    def kill_reason(self) -> str | None:
        return self._kill_reason

    @property
    def spending_blocked(self) -> str | None:
        return self._spend_block

    async def submit(self, req: ActionRequest) -> ActionResult:
        if self._closed:
            return ActionResult(ActionStatus.SUPPRESSED, reason="shutdown")
        key = req.idempotency_key
        if key is not None:
            shared = self._keys.get(key)
            if shared is None:
                known = await self._store.get_by_key(key)
                if known is not None:
                    return known.to_result()
                shared = self._keys.get(key)
            if shared is not None:
                return await asyncio.shield(shared)
        eng = self._settings.current.engine
        pending = _Pending(
            req=req,
            cls=command_class(req),
            seq=next(self._seq),
            enqueued=time.monotonic(),
            ttl=req.ttl_s if req.ttl_s is not None else eng.action_ttl_s,
            future=asyncio.get_running_loop().create_future(),
        )
        if key is not None:
            self._keys[key] = pending.future

            def forget(_: asyncio.Future[ActionResult]) -> None:
                self._keys.pop(key, None)

            pending.future.add_done_callback(forget)
        async with self._cond:
            self._queue.append(pending)
            self._cond.notify_all()
        try:
            return await asyncio.shield(pending.future)
        except asyncio.CancelledError:
            await self._withdraw(pending)
            raise

    async def acquire_lease(self, owner: str) -> Lease:
        async with self._cond:
            await self._cond.wait_for(lambda: self._lease is None)
            self._lease = Lease(owner)
            return self._lease

    async def release_lease(self, lease: Lease) -> None:
        async with self._cond:
            if self._lease is lease:
                self._lease = None
            self._cond.notify_all()

    async def set_safe_point(self, lease: Lease, safe: bool) -> None:
        async with self._cond:
            lease.safe = safe
            self._cond.notify_all()

    async def wake(self) -> None:
        async with self._cond:
            self._cond.notify_all()

    async def kill(self, reason: str) -> int:
        self._kill_reason = reason
        return await self.cancel_queued("kill_switch")

    async def unkill(self) -> None:
        self._kill_reason = None
        await self.wake()

    def block_spending(self, reason: str) -> None:
        self._spend_block = reason

    async def allow_spending(self) -> None:
        self._spend_block = None
        await self.wake()

    async def cancel_queued(self, reason: str) -> int:
        async with self._cond:
            dropped, self._queue = self._queue, []
        for p in dropped:
            self._resolve(p, await self._record(p, ActionStatus.SUPPRESSED, reason))
        return len(dropped)

    async def shutdown(self) -> None:
        self._closed = True
        await self.cancel_queued("shutdown")

    async def on_delivery(self, delivery: Delivery) -> None:
        inflight = self._inflight
        msg = delivery.msg
        if inflight is None or msg.outgoing or msg.chat_id != inflight.req.chat_id:
            return
        if delivery.journal_id <= inflight.boundary or msg.date < inflight.sent_at - DATE_SKEW:
            return
        if any(isinstance(e, AntiFlood) for e in delivery.events):
            inflight.antiflood.set()
            return
        expect = inflight.req.expect
        if expect is None or inflight.matched.done():
            return
        match = expect.predicate(delivery)
        if match is not None:
            inflight.matched.set_result(match)

    async def run(self) -> None:
        while True:
            pending = await self._next()
            try:
                result = await self._execute(pending)
            except asyncio.CancelledError:
                self._resolve(
                    pending, ActionResult(ActionStatus.OUTCOME_UNKNOWN, reason="cancelled")
                )
                raise
            except Exception:
                log.exception("gateway failed to execute action")
                result = ActionResult(ActionStatus.OUTCOME_UNKNOWN, reason="internal_error")
            self._resolve(pending, result)

    def _blocked(self, p: _Pending) -> Blocked | None:
        req, cls = p.req, p.cls
        eng = self._settings.current.engine
        if cls in (CommandClass.FORBIDDEN, CommandClass.DONATE):
            return ActionStatus.REJECTED, cls.value
        if cls is CommandClass.RISKY and not (req.source is Source.MANUAL and req.risky_confirmed):
            return ActionStatus.REJECTED, "risky_requires_confirm"
        if cls is not CommandClass.NAV and req.expect is None:
            return ActionStatus.REJECTED, "expectation_required"
        if self._kill_reason is not None or eng.killed:
            return ActionStatus.SUPPRESSED, "kill_switch"
        if eng.mode == "dry_run" and cls is not CommandClass.NAV:
            return ActionStatus.SUPPRESSED, "dry_run"
        if self._spend_block is not None and cls is not CommandClass.NAV:
            return ActionStatus.REJECTED, f"blocked:{self._spend_block}"
        if time.monotonic() - p.enqueued >= p.ttl:
            return ActionStatus.REJECTED, "expired"
        if req.kind is ActionKind.CLICK:
            latest = self._latest(req.chat_id, req.message_id or 0)
            if latest is None or req.data is None or latest.button(req.data) is None:
                return ActionStatus.REJECTED, "stale_button"
            if req.expect_revision is not None and latest.revision != req.expect_revision:
                return ActionStatus.REJECTED, "stale_revision"
        return None

    def _eligible(self, p: _Pending) -> bool:
        lease = self._lease
        if lease is None or p.req.lease_token == lease.token:
            return True
        return lease.safe and p.req.source in (Source.URGENT, Source.MANUAL)

    def _nearest_deadline(self) -> float | None:
        if not self._queue:
            return None
        now = time.monotonic()
        return max(0.005, min(p.enqueued + p.ttl - now for p in self._queue))

    async def _next(self) -> _Pending:
        while True:
            async with self._cond:
                terminal = [(p, b) for p in self._queue if (b := self._blocked(p)) is not None]
                for p, _ in terminal:
                    self._queue.remove(p)
                if not terminal:
                    ready = [p for p in self._queue if self._eligible(p)]
                    if ready:
                        chosen = min(ready, key=lambda p: (p.req.source, p.seq))
                        self._queue.remove(chosen)
                        return chosen
                    try:
                        await asyncio.wait_for(self._cond.wait(), self._nearest_deadline())
                    except TimeoutError:
                        pass
                    continue
            for p, (status, reason) in terminal:
                self._resolve(p, await self._record(p, status, reason))

    async def _withdraw(self, pending: _Pending) -> None:
        async with self._cond:
            queued = pending in self._queue
            if queued:
                self._queue.remove(pending)
        if queued:
            result = await self._record(pending, ActionStatus.SUPPRESSED, "cancelled")
            self._resolve(pending, result)

    def _resolve(self, p: _Pending, result: ActionResult) -> None:
        if not p.future.done():
            p.future.set_result(result)

    async def _execute(self, p: _Pending) -> ActionResult:
        blocked = self._blocked(p)
        if blocked is not None:
            return await self._record(p, *blocked)
        action_id: int | None
        try:
            action_id = await self._store.create(p.req, p.cls, ActionStatus.INTENT)
        except DuplicateKey as dup:
            return dup.existing.to_result()
        except Exception:
            log.exception("intent not persisted")
            if p.cls is not CommandClass.NAV:
                return ActionResult(ActionStatus.REJECTED, reason="db_unavailable")
            action_id = None
        return await self._attempts(p, action_id)

    async def _record(self, p: _Pending, status: ActionStatus, reason: str) -> ActionResult:
        try:
            action_id: int | None = await self._store.create(p.req, p.cls, status, reason)
        except DuplicateKey as dup:
            return dup.existing.to_result()
        except Exception:
            log.exception("action record not persisted")
            action_id = None
        return ActionResult(status, action_id=action_id, reason=reason)

    async def _attempts(self, p: _Pending, action_id: int | None) -> ActionResult:
        req = p.req
        attempts = 0
        while True:
            attempts += 1
            await self._pace()
            blocked = self._blocked(p)
            if blocked is not None:
                return await self._finish(action_id, *blocked)
            eng = self._settings.current.engine
            inflight = _InFlight(
                req=req,
                sent_at=self._clock.now(),
                boundary=self._boundary(),
                matched=asyncio.get_running_loop().create_future(),
            )
            self._inflight = inflight
            answer: str | None = None
            try:
                try:
                    answer = await self._transmit(req, eng.click_answer_timeout_s)
                except FloodWait as fw:
                    left = p.ttl - (time.monotonic() - p.enqueued)
                    if fw.seconds > MAX_FLOODWAIT_S or fw.seconds >= left:
                        return await self._finish(
                            action_id, ActionStatus.REFUSED, f"flood_wait:{fw.seconds:.0f}"
                        )
                    self._inflight = None
                    await asyncio.sleep(fw.seconds)
                    continue
                except TransportAuthLost:
                    return await self._finish(action_id, ActionStatus.REFUSED, "auth_lost")
                except TransportRejected as exc:
                    return await self._finish(action_id, ActionStatus.REFUSED, f"rejected:{exc}")
                except Exception as exc:
                    return await self._finish(
                        action_id, ActionStatus.OUTCOME_UNKNOWN, f"send_error:{type(exc).__name__}"
                    )
                await self._mark_sent(action_id, attempts, answer)
                if req.expect is None:
                    return await self._finish(
                        action_id, ActionStatus.CONFIRMED, "sent", answer=answer
                    )
                outcome = await self._await_outcome(inflight, req.expect.timeout_s)
            finally:
                self._inflight = None
            if outcome == "antiflood":
                if attempts <= eng.antiflood_retry_max and time.monotonic() - p.enqueued < p.ttl:
                    await asyncio.sleep(eng.antiflood_pause_s)
                    continue
                return await self._finish(
                    action_id, ActionStatus.OUTCOME_UNKNOWN, "antiflood", answer=answer
                )
            if outcome is None:
                return await self._finish(
                    action_id, ActionStatus.OUTCOME_UNKNOWN, "timeout", answer=answer
                )
            status = (
                ActionStatus.CONFIRMED
                if outcome.verdict is Verdict.CONFIRMED
                else ActionStatus.REFUSED
            )
            return await self._finish(
                action_id, status, outcome.detail, answer=answer, match=outcome
            )

    async def _transmit(self, req: ActionRequest, click_timeout: float) -> str | None:
        if req.kind is ActionKind.SEND:
            await self._transport.send_text(req.chat_id, req.text or "", req.reply_to)
            return None
        return await self._transport.click(
            req.chat_id, req.message_id or 0, req.data or "", click_timeout
        )

    async def _await_outcome(
        self, inflight: _InFlight, timeout_s: float
    ) -> Match | Literal["antiflood"] | None:
        flood: asyncio.Future[Any] = asyncio.ensure_future(inflight.antiflood.wait())
        waiters: set[asyncio.Future[Any]] = {inflight.matched, flood}
        try:
            done, _ = await asyncio.wait(
                waiters, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            flood.cancel()
        if inflight.matched in done:
            return inflight.matched.result()
        if flood in done:
            return "antiflood"
        return None

    async def _pace(self) -> None:
        interval = self._settings.current.engine.min_request_interval_s
        wait = self._last_send + interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_send = time.monotonic()

    async def _mark_sent(self, action_id: int | None, attempts: int, answer: str | None) -> None:
        if action_id is None:
            return
        try:
            await self._store.update(
                action_id, status=ActionStatus.SENT, attempts=attempts, answer=answer, sent=True
            )
        except Exception:
            log.exception("sent status not persisted for %s", action_id)

    async def _finish(
        self,
        action_id: int | None,
        status: ActionStatus,
        reason: str,
        *,
        answer: str | None = None,
        match: Match | None = None,
    ) -> ActionResult:
        if action_id is not None:
            try:
                await self._store.update(
                    action_id,
                    status=status,
                    reason=reason,
                    answer=answer,
                    match_detail=match.detail if match else None,
                )
            except Exception:
                log.exception("final status not persisted for %s", action_id)
        return ActionResult(status, action_id=action_id, reason=reason, match=match, answer=answer)
