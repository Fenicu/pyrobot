from __future__ import annotations

import asyncio
import itertools
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Literal

from app.engine.bus import Delivery
from app.engine.clock import Clock
from app.engine.commands import (
    CommandClass,
    classify_callback,
    classify_text,
    feature_of_callback,
    feature_of_text,
    spends_nothing_callback,
)
from app.engine.events import AntiFlood
from app.engine.gateway.store import CANCELLED, ActionStore, DuplicateKey
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
CanSend = Callable[[], str | None]
StateVersion = Callable[[], int]
# Своя компания из профиля (код, как в /buys_<код>_N); None — пока неизвестна.
OwnCompany = Callable[[], str | None]
Blocked = tuple[ActionStatus, str]
UncertainHook = Callable[[ActionRequest, int | None], None]
DATE_SKEW = timedelta(seconds=2)
MAX_FLOODWAIT_S = 300.0
MAX_KEY_LEN = 100
_NEXT_ERROR_PAUSE_S = 0.05
_ABANDON_WRITE_S = 5.0
RECONCILE_REASON = "reconcile_required"
# Ключ ручного действия не записан: запрос проваливается, повтор тем же ключом безопасен.
STORE_FAILED = "store_failed"
# Чтение источника пересылки: клиент Telegram сам повторяет запросы (до ~160 с) — шлюз столько
# не ждёт, не прочитали — не пересылаем.
SOURCE_READ_TIMEOUT_S = 10.0
# Клик «👍Стартуем!» экрана пересборки артефакта.
ARTIFACT_ACCEPT = re.compile(r"artr_(book|fax|light)_accept\Z")


class NotSent(Exception):
    """Отправка остановлена последней проверкой в транспорте: у пересылки исходное сообщение не
    то, что видела реакция (правлено, удалено, не читается), или за время чтения что-то изменилось
    (чат команды, kill, срок); у команды и клика — пока разрешался peer (класс команды акций, kill,
    срок) или peer не разрешился (`peer_unresolved`). До RPC дело не дошло."""

    def __init__(self, status: ActionStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(eq=False)
class _Pending:
    req: ActionRequest
    cls: CommandClass
    seq: int
    enqueued: float
    ttl: float
    future: asyncio.Future[ActionResult]
    action_id: int | None = None
    finished: bool = False


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


def _always_can_send() -> str | None:
    return None


def _company_unknown() -> str | None:
    return None


def _keyed_manual(req: ActionRequest) -> bool:
    return req.source is Source.MANUAL and req.idempotency_key is not None


def command_class(req: ActionRequest, own_company: str | None = None) -> CommandClass:
    if req.kind is ActionKind.FORWARD:
        return CommandClass.FORWARD
    if req.kind is ActionKind.SEND:
        return classify_text(req.text or "", own_company)
    return classify_callback(req.data or "", own_company)


def _answer_chat(req: ActionRequest) -> int:
    expect = req.expect
    return expect.chat_id if expect is not None and expect.chat_id is not None else req.chat_id


def spends_nothing(req: ActionRequest) -> bool:
    if req.kind is ActionKind.FORWARD:
        return True
    return req.kind is ActionKind.CLICK and spends_nothing_callback(req.data or "")


def command_feature(req: ActionRequest) -> str | None:
    if req.kind is ActionKind.FORWARD:
        return None
    if req.kind is ActionKind.SEND:
        return feature_of_text(req.text or "")
    return feature_of_callback(req.data or "")


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
        can_send: CanSend = _always_can_send,
        on_uncertain: UncertainHook | None = None,
        state_version: StateVersion | None = None,
        own_company: OwnCompany = _company_unknown,
    ) -> None:
        self._transport = transport
        self._own_company = own_company
        self._can_send = can_send
        self._state_version = state_version
        self._store = store
        self._settings = settings
        self._latest = latest
        self._boundary = boundary
        self._clock = clock
        self.on_uncertain = on_uncertain
        self._queue: list[_Pending] = []
        self._cond = asyncio.Condition()
        self._seq = itertools.count()
        self._inflight: _InFlight | None = None
        self._lease: Lease | None = None
        self._last_send = 0.0
        self._paused_until = 0.0
        self._kill_reason: str | None = None
        self._spend_block: str | None = None
        self._keys: dict[str, asyncio.Future[ActionResult]] = {}
        self._closed = False
        # Проверенный чат команды (группа, аккаунт в ней состоит), версия настроек, при которой
        # проверен (любое сохранение, в том числе A → B → A, требует новой проверки), и название.
        self._group_ok: tuple[int, int, str | None] | None = None

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    @property
    def in_flight(self) -> ActionRequest | None:
        return self._inflight.req if self._inflight else None

    @property
    def lease(self) -> Lease | None:
        return self._lease

    def latest(self, chat_id: int, message_id: int) -> IncomingMessage | None:
        """Последняя известная ревизия сообщения — та, по кнопкам которой проверяется клик."""
        return self._latest(chat_id, message_id)

    @property
    def kill_reason(self) -> str | None:
        return self._kill_reason

    @property
    def spending_blocked(self) -> str | None:
        return self._spend_block

    async def submit(self, req: ActionRequest) -> ActionResult:
        key = req.idempotency_key
        if key is not None and len(key) > MAX_KEY_LEN:
            return ActionResult(ActionStatus.REJECTED, reason="bad_key")
        if key is not None:
            shared = self._live_shared(key)
            if shared is None:
                try:
                    known = await self._store.get_by_key(key)
                except Exception:
                    log.exception("idempotency lookup failed for key %s", key)
                    known = None
                if known is not None:
                    return known.to_result()
                shared = self._live_shared(key)
            if shared is not None:
                return await asyncio.shield(shared)
        eng = self._settings.current.engine
        cls = command_class(req, self._own_company())
        pending = _Pending(
            req=req,
            cls=cls,
            seq=next(self._seq),
            enqueued=self._clock.monotonic(),
            ttl=req.ttl_s if req.ttl_s is not None else eng.action_ttl_s,
            future=asyncio.get_running_loop().create_future(),
        )
        if key is not None:
            self._keys[key] = pending.future

            def forget(fut: asyncio.Future[ActionResult]) -> None:
                if self._keys.get(key) is fut:
                    self._keys.pop(key, None)

            pending.future.add_done_callback(forget)
        blocked = self._guarded(lambda: self._static_checks(req, cls))
        if blocked is not None:
            return await self._reject(pending, *blocked)
        async with self._cond:
            if self._closed:
                closed_now = True
            else:
                self._queue.append(pending)
                self._cond.notify_all()
                closed_now = False
        if closed_now:
            return await self._reject(pending, ActionStatus.SUPPRESSED, "shutdown")
        try:
            return await asyncio.shield(pending.future)
        except asyncio.CancelledError:
            if req.idempotency_key is None:
                await self._withdraw(pending)
            raise

    def pending_key(self, key: str) -> bool:
        """Действие с этим ключом идемпотентности ещё не завершено в этом процессе."""
        return self._live_shared(key) is not None

    def _live_shared(self, key: str) -> asyncio.Future[ActionResult] | None:
        # done_callback снимает ключ через call_soon — без .done() можно поймать
        # уже отработавший future раньше, чем сработает его собственная очистка.
        shared = self._keys.get(key)
        return shared if shared is not None and not shared.done() else None

    async def _reject(self, p: _Pending, status: ActionStatus, reason: str) -> ActionResult:
        result = await self._record(p, status, reason)
        self._resolve(p, result)
        return result

    def _guarded(self, fn: Callable[[], Blocked | None]) -> Blocked | None:
        try:
            return fn()
        except Exception as exc:
            log.exception("action check failed")
            return ActionStatus.REJECTED, f"check_failed:{type(exc).__name__}"

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
        try:
            for p in dropped:
                if not p.future.done():
                    self._resolve(p, await self._record(p, ActionStatus.SUPPRESSED, reason))
        finally:
            for p in dropped:
                self._resolve(p, ActionResult(ActionStatus.SUPPRESSED, reason=reason))
        return len(dropped)

    async def shutdown(self) -> None:
        self._closed = True
        await self.cancel_queued("shutdown")

    async def on_delivery(self, delivery: Delivery) -> None:
        inflight = self._inflight
        msg = delivery.msg
        if inflight is None or msg.outgoing or msg.chat_id != _answer_chat(inflight.req):
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
            try:
                pending = await self._next()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("gateway failed to pick next action")
                await asyncio.sleep(_NEXT_ERROR_PAUSE_S)
                continue
            try:
                result = await self._execute(pending)
            except asyncio.CancelledError:
                self._resolve(pending, await self._abandon(pending, CANCELLED))
                raise
            except Exception:
                log.exception("gateway failed to execute action")
                result = await self._abandon(pending, "internal_error")
            self._resolve(pending, result)

    async def _abandon(self, p: _Pending, reason: str) -> ActionResult:
        # Строка, уже дошедшая до INTENT/SENT, закрывается как outcome_unknown сразу, а не
        # при следующем старте; CANCELLED при старте всё равно требует сверки.
        if p.action_id is not None and not p.finished:
            try:
                async with asyncio.timeout(_ABANDON_WRITE_S):
                    await self._store.update(
                        p.action_id, status=ActionStatus.OUTCOME_UNKNOWN, reason=reason
                    )
            except Exception:
                log.exception("abandoned action %s not persisted", p.action_id)
        self._uncertain(p)
        return ActionResult(ActionStatus.OUTCOME_UNKNOWN, action_id=p.action_id, reason=reason)

    def _uncertain(self, p: _Pending) -> None:
        # Исход траты неизвестен: до сверки состояния новые траты запрещены. Блок ставится
        # синхронно, до выбора следующего действия. Пересылка ничего в игре не тратит: её
        # неизвестный исход не сверяется и не повторяется.
        if p.cls in (CommandClass.NAV, CommandClass.FORWARD):
            return
        self._spend_block = RECONCILE_REASON
        if self.on_uncertain is not None:
            try:
                self.on_uncertain(p.req, p.action_id)
            except Exception:
                log.exception("uncertain hook failed")

    def _static_checks(self, req: ActionRequest, cls: CommandClass) -> Blocked | None:
        eng = self._settings.current.engine
        if self._closed:
            return ActionStatus.SUPPRESSED, "shutdown"
        if cls is CommandClass.FORWARD:
            return self._forward_checks(req)
        if cls in (CommandClass.FORBIDDEN, CommandClass.DONATE):
            return ActionStatus.REJECTED, cls.value
        if req.chat_id not in self._allowed_chats():
            return ActionStatus.REJECTED, "chat_not_allowed"
        if cls is CommandClass.RISKY and not (
            (req.source is Source.MANUAL and req.risky_confirmed) or self._artifact_start(req)
        ):
            return ActionStatus.REJECTED, "risky_requires_confirm"
        if self._confirm_stale(req):
            return ActionStatus.REJECTED, "confirm_stale"
        if cls is not CommandClass.NAV and req.expect is None:
            return ActionStatus.REJECTED, "expectation_required"
        if self._kill_reason is not None or eng.killed:
            return ActionStatus.SUPPRESSED, "kill_switch"
        cannot = self._can_send()
        if cannot is not None:
            return ActionStatus.REJECTED, cannot
        if cls is not CommandClass.NAV:
            blocked = self._policy_checks(req)
            if blocked is not None:
                return blocked
        if (eng.mode == "dry_run" or req.dry_run) and cls is not CommandClass.NAV:
            return ActionStatus.SUPPRESSED, "dry_run"
        if req.simulate and cls is not CommandClass.NAV:
            return ActionStatus.SUPPRESSED, "uncertified"
        if (
            self._spend_block is not None
            and cls is not CommandClass.NAV
            and not spends_nothing(req)
        ):
            return ActionStatus.REJECTED, f"blocked:{self._spend_block}"
        return None

    def _forward_checks(self, req: ActionRequest) -> Blocked | None:
        """Пересылка — только из чата игры и только в текущий чат команды (сверяется и перед
        каждой попыткой), с хешем содержимого, которое видела реакция. Kill отклоняет, dry_run
        подавляет; пауза и блок трат не мешают: в игре пересылка ничего не меняет."""
        current = self._settings.current
        chats = current.chats
        if req.message_id is None or req.expect_content is None:
            return ActionStatus.REJECTED, "forward_invalid"
        if req.from_chat_id != chats.game_chat_id:
            return ActionStatus.REJECTED, "forward_source"
        if chats.team_chat_id is None:
            return ActionStatus.REJECTED, "team_chat_off"
        if req.chat_id != chats.team_chat_id:
            return ActionStatus.REJECTED, "team_chat_changed"
        if self._kill_reason is not None or current.engine.killed:
            return ActionStatus.REJECTED, "kill_switch"
        cannot = self._can_send()
        if cannot is not None:
            return ActionStatus.REJECTED, cannot
        if current.engine.mode == "dry_run" or req.dry_run:
            return ActionStatus.SUPPRESSED, "dry_run"
        return None

    def _confirm_stale(self, req: ActionRequest) -> bool:
        if req.confirm_until is not None and self._clock.now() > req.confirm_until:
            return True
        if req.confirm_version is None:
            return False
        current = self._state_version() if self._state_version is not None else None
        return current != req.confirm_version

    def _artifact_start(self, req: ActionRequest) -> bool:
        """Старт сбора артефакта — только шагом его сценария и только пока запись сбора ждёт
        запуска этого артефакта: отмена в админке до отправки клика его не пропустит."""
        if req.kind is not ActionKind.CLICK or req.scenario != "artifact_start":
            return False
        m = ARTIFACT_ACCEPT.match(req.data or "")
        run = self._settings.current.artifact_run
        return m is not None and run.status == "starting" and run.artifact == m[1]

    def _policy_checks(self, req: ActionRequest) -> Blocked | None:
        current = self._settings.current
        eng = current.engine
        if eng.paused:
            # Планировщик и сценарии стоят (шаг сценария, начатого до паузы, тоже не уходит).
            allowed = {
                Source.URGENT: eng.urgent_while_paused,
                Source.MANUAL: eng.manual_while_paused,
            }.get(req.source, False)
            if not allowed:
                return ActionStatus.REJECTED, "paused"
        feature = command_feature(req)
        if (
            feature is not None
            and req.source is not Source.MANUAL
            and not getattr(current.features, feature, False)
        ):
            return ActionStatus.REJECTED, f"feature_off:{feature}"
        return None

    def _allowed_chats(self) -> set[int]:
        chats = self._settings.current.chats
        allowed = {chats.game_chat_id, chats.tangerine_chat_id}
        if chats.bulls_invite_chat_id is not None:
            allowed.add(chats.bulls_invite_chat_id)
        return allowed

    def _check(self, p: _Pending) -> Blocked | None:
        def full_check() -> Blocked | None:
            # Класс команд акций зависит от своей компании, а профиль мог прийти, пока команда
            # ждала очереди или повтора: перед каждой отправкой — по текущему состоянию.
            p.cls = command_class(p.req, self._own_company())
            blocked = self._static_checks(p.req, p.cls)
            if blocked is not None:
                return blocked
            if self._clock.monotonic() - p.enqueued >= p.ttl:
                return ActionStatus.REJECTED, "expired"
            if p.req.deadline is not None and self._clock.now() >= p.req.deadline:
                return ActionStatus.REJECTED, "deadline"
            if p.req.kind is ActionKind.CLICK:
                latest = self._latest(p.req.chat_id, p.req.message_id or 0)
                if latest is None or p.req.data is None or latest.button(p.req.data) is None:
                    return ActionStatus.REJECTED, "stale_button"
                if p.req.expect_revision is not None and latest.revision != p.req.expect_revision:
                    return ActionStatus.REJECTED, "stale_revision"
                expected = p.req.expect_content
                if expected is not None and latest.content_hash() != expected:
                    return ActionStatus.REJECTED, "stale_content"
            return None

        return self._guarded(full_check)

    def _eligible(self, p: _Pending) -> bool:
        lease = self._lease
        # Пересылка экран игры не трогает: шагам сценария под арендой она не мешает.
        if lease is None or p.req.lease_token == lease.token or p.cls is CommandClass.FORWARD:
            return True
        return lease.safe and p.req.source in (Source.URGENT, Source.MANUAL)

    def _nearest_deadline(self) -> float | None:
        if not self._queue:
            return None
        now = self._clock.monotonic()
        return max(0.005, min(p.enqueued + p.ttl - now for p in self._queue))

    async def _next(self) -> _Pending:
        while True:
            async with self._cond:
                terminal = [(p, b) for p in self._queue if (b := self._check(p)) is not None]
                for p, _ in terminal:
                    self._queue.remove(p)
                if not terminal:
                    # Во время паузы (FloodWait/антифлуд) действие не выбирается и INTENT не
                    # пишется, но TTL стоящих в очереди продолжает истекать.
                    paused = self._paused_until - self._clock.monotonic()
                    ready = [] if paused > 0 else [p for p in self._queue if self._eligible(p)]
                    if ready:
                        chosen = min(ready, key=lambda p: (p.req.source, p.seq))
                        self._queue.remove(chosen)
                        lease = self._lease
                        if lease is not None and chosen.req.lease_token == lease.token:
                            # Безопасная точка длится до выбора следующего шага аренды: срочное
                            # и ручное, поданные до него, уже прошли вперёд по приоритету.
                            lease.safe = False
                        return chosen
                    timeout = self._nearest_deadline()
                    if paused > 0:
                        timeout = paused if timeout is None else min(timeout, paused)
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout)
                    except TimeoutError:
                        pass
                    continue
            try:
                for p, (status, reason) in terminal:
                    self._resolve(p, await self._record(p, status, reason))
            finally:
                for p, (status, reason) in terminal:
                    self._resolve(p, ActionResult(status, reason=reason))

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
        blocked = self._check(p)
        if blocked is not None:
            return await self._record(p, *blocked)
        if p.cls is CommandClass.FORWARD:
            chat, version = p.req.chat_id, self._settings.version
            known = self._group_ok
            if known is not None and known[:2] == (chat, version):
                title = known[2]
            else:
                verdict, title = await self._check_group(p)
                if verdict != "ok":
                    p.req = replace(p.req, chat_title=title)
                    log.warning("team chat %s (%r) refused: %s", chat, title, verdict)
                    return await self._record(p, ActionStatus.REFUSED, f"team_chat_{verdict}")
                log.info("team chat %s verified: %r", chat, title)
                self._group_ok = (chat, version, title)
            p.req = replace(p.req, chat_title=title)
        try:
            p.action_id = await self._store.create(p.req, p.cls, ActionStatus.INTENT)
        except DuplicateKey as dup:
            return dup.existing.to_result()
        except Exception:
            log.exception("intent not persisted")
            if _keyed_manual(p.req):
                return ActionResult(ActionStatus.REJECTED, reason=STORE_FAILED)
            if p.cls is not CommandClass.NAV:
                return ActionResult(ActionStatus.REJECTED, reason="db_unavailable")
        return await self._attempts(p)

    async def _check_group(self, p: _Pending) -> tuple[str, str | None]:
        """Перед первой пересылкой в чат (и первой после любого сохранения настроек): группа или
        супергруппа, где аккаунт — участник. Запросы идут в общем темпе шлюза; FloodWait ставит
        паузу шлюза и проверку повторяет в пределах TTL. Отказ не кешируется — после добавления
        аккаунта в группу следующая пересылка проверит заново."""
        chat_id = p.req.chat_id
        while True:
            await self._pace()
            try:
                info = await self._transport.check_group(chat_id)
                return info.verdict, info.title
            except FloodWait as fw:
                now = self._clock.monotonic()
                self._paused_until = max(self._paused_until, now + fw.seconds)
                left = p.ttl - (now - p.enqueued)
                if fw.seconds > MAX_FLOODWAIT_S or fw.seconds >= left:
                    return "flood_wait", None
            except TransportAuthLost:
                return "auth_lost", None
            except Exception:
                log.exception("team chat %s not checked", chat_id)
                return "unavailable", None

    async def _record(self, p: _Pending, status: ActionStatus, reason: str) -> ActionResult:
        # Действие не дошло до INTENT — ключ идемпотентности не расходуется, чтобы тем же ключом
        # можно было повторить попытку (например, после dry_run). Кроме ручных: для API повтор
        # с тем же ключом возвращает прежний итог.
        manual = _keyed_manual(p.req)
        req_for_store = p.req if manual else replace(p.req, idempotency_key=None)
        try:
            action_id: int | None = await self._store.create(req_for_store, p.cls, status, reason)
        except DuplicateKey as dup:
            return dup.existing.to_result()
        except Exception:
            log.exception("action record not persisted")
            # Итог ручного действия без сохранённого ключа нельзя отдавать: повтор тем же
            # ключом уже в live отправил бы команду, подавленную сейчас.
            if manual:
                return ActionResult(ActionStatus.REJECTED, reason=STORE_FAILED)
            action_id = None
        return ActionResult(status, action_id=action_id, reason=reason)

    async def _attempts(self, p: _Pending) -> ActionResult:
        req = p.req
        attempts = 0
        antiflood_tries = 0
        while True:
            attempts += 1
            await self._pace()
            blocked = self._check(p)
            if blocked is not None:
                return await self._finish(p, *blocked)
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
                    answer = await self._transmit(p, eng.click_answer_timeout_s)
                except FloodWait as fw:
                    now = self._clock.monotonic()
                    self._paused_until = max(self._paused_until, now + fw.seconds)
                    left = p.ttl - (now - p.enqueued)
                    if fw.seconds > MAX_FLOODWAIT_S or fw.seconds >= left:
                        return await self._finish(
                            p, ActionStatus.REFUSED, f"flood_wait:{fw.seconds:.0f}"
                        )
                    self._inflight = None
                    continue
                except TransportAuthLost:
                    return await self._finish(p, ActionStatus.REFUSED, "auth_lost")
                except TransportRejected as exc:
                    return await self._finish(p, ActionStatus.REFUSED, f"rejected:{exc}")
                except NotSent as exc:
                    return await self._finish(p, exc.status, exc.reason)
                except Exception as exc:
                    return await self._finish(
                        p, ActionStatus.OUTCOME_UNKNOWN, f"send_error:{type(exc).__name__}"
                    )
                await self._mark_sent(p, attempts, answer)
                if req.expect is None:
                    return await self._finish(p, ActionStatus.CONFIRMED, "sent", answer=answer)
                timeout_s = (
                    req.expect.timeout_s
                    if req.expect.timeout_s is not None
                    else eng.default_expect_timeout_s
                )
                outcome = await self._await_outcome(inflight, timeout_s)
            finally:
                self._inflight = None
            if outcome == "antiflood":
                antiflood_tries += 1
                # Пауза ставится независимо от исхода ниже — и на повтор, и на отказ,
                # антифлуд от игры должен придержать весь шлюз, а не только эту заявку.
                now = self._clock.monotonic()
                self._paused_until = max(self._paused_until, now + eng.antiflood_pause_s)
                within_ttl = now - p.enqueued < p.ttl
                if antiflood_tries <= eng.antiflood_retry_max and within_ttl:
                    continue
                return await self._finish(
                    p, ActionStatus.OUTCOME_UNKNOWN, "antiflood", answer=answer
                )
            if outcome is None:
                if req.expect.silence_confirms:
                    return await self._finish(p, ActionStatus.CONFIRMED, "silence", answer=answer)
                return await self._finish(
                    p, ActionStatus.OUTCOME_UNKNOWN, "timeout", answer=answer
                )
            status = (
                ActionStatus.CONFIRMED
                if outcome.verdict is Verdict.CONFIRMED
                else ActionStatus.REFUSED
            )
            return await self._finish(p, status, outcome.detail, answer=answer, match=outcome)

    async def _transmit(self, p: _Pending, click_timeout: float) -> str | None:
        req = p.req
        if req.kind is ActionKind.FORWARD:
            await self._check_source(req)
            # Чтение — сетевой запрос: пока ответ был в пути, могли смениться чат команды, kill,
            # режим, срок. Последняя сверка — синхронно, прямо перед вызовом транспорта.
            blocked = self._check(p)
            if blocked is not None:
                raise NotSent(*blocked)
            # Ответ пересылки — id сообщения в чате назначения (0 — Telegram его не вернул).
            sent = await self._transport.forward(
                req.from_chat_id or 0, req.message_id or 0, req.chat_id
            )
            return str(sent) if sent else None
        # Peer — заранее, а последняя проверка — после него: профиль, пришедший, пока peer
        # разрешался, меняет класс команды акций. От проверки до RPC ожиданий нет.
        try:
            await self._transport.resolve(req.chat_id)
        except (FloodWait, TransportAuthLost):
            raise
        except Exception as exc:
            log.warning("peer of chat %s not resolved: %r", req.chat_id, exc)
            raise NotSent(ActionStatus.REFUSED, f"peer_unresolved:{type(exc).__name__}") from exc
        blocked = self._check(p)
        if blocked is not None:
            raise NotSent(*blocked)
        if req.kind is ActionKind.SEND:
            await self._transport.send_text(req.chat_id, req.text or "", req.reply_to)
            return None
        return await self._transport.click(
            req.chat_id, req.message_id or 0, req.data or "", click_timeout
        )

    async def _check_source(self, req: ActionRequest) -> None:
        """Перед каждой попыткой пересылки исходное сообщение перечитывается из Telegram: правленое
        после реакции, удалённое или непрочитанное (в том числе за `SOURCE_READ_TIMEOUT_S`) не
        пересылается. Правка между этим чтением и
        ForwardMessages остаётся гонкой — Telegram перешлёт текущую версию."""
        try:
            async with asyncio.timeout(SOURCE_READ_TIMEOUT_S):
                current = await self._transport.fetch(req.from_chat_id or 0, req.message_id or 0)
        except (FloodWait, TransportAuthLost):
            raise
        except Exception as exc:
            log.warning("forward source %s not read: %r", req.message_id, exc)
            raise NotSent(ActionStatus.REFUSED, "source_unreadable") from exc
        if current is None:
            raise NotSent(ActionStatus.REFUSED, "source_gone")
        if current.content_hash() != req.expect_content:
            raise NotSent(ActionStatus.REFUSED, "source_changed")

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
        target = max(self._last_send + interval, self._paused_until)
        wait = target - self._clock.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_send = self._clock.monotonic()

    async def _mark_sent(self, p: _Pending, attempts: int, answer: str | None) -> None:
        action_id = p.action_id
        if action_id is None:
            return
        try:
            await self._store.update(
                action_id,
                status=ActionStatus.SENT,
                attempts=attempts,
                answer=answer,
                sent=True,
                cls=p.cls,
            )
        except Exception:
            log.exception("sent status not persisted for %s", action_id)

    async def _finish(
        self,
        p: _Pending,
        status: ActionStatus,
        reason: str,
        *,
        answer: str | None = None,
        match: Match | None = None,
    ) -> ActionResult:
        p.finished = True
        if status is ActionStatus.OUTCOME_UNKNOWN:
            self._uncertain(p)
        action_id = p.action_id
        if action_id is not None:
            try:
                await self._store.update(
                    action_id,
                    status=status,
                    reason=reason,
                    answer=answer,
                    match_detail=match.detail if match else None,
                    cls=p.cls,
                )
            except Exception:
                log.exception("final status not persisted for %s", action_id)
        return ActionResult(status, action_id=action_id, reason=reason, match=match, answer=answer)
