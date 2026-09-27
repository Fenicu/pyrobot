from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.engine.bus import Delivery
from app.engine.clock import Clock, SystemClock
from app.engine.gateway.gateway import ActionGateway
from app.engine.gateway.types import (
    ActionKind,
    ActionRequest,
    ActionStatus,
    Expectation,
    Match,
    Predicate,
    Source,
    Verdict,
)
from app.engine.notify import NotifierPort
from app.engine.parsing.sleep import RobberyAlert, RobberyFight
from app.engine.settings import SettingsProvider
from app.engine.types import IncomingMessage

log = logging.getLogger(__name__)
Reread = Callable[[int, int], Awaitable[IncomingMessage | None]]
# Последние записанные ревизии сообщений с тревогой, созданных не раньше момента.
Alerts = Callable[[datetime], Awaitable[list[IncomingMessage]]]
WAKE_BUTTON = re.compile(r"rob_awake_\d+\Z")
# Итог драки игра присылает правкой за 4–8 с после клика.
FIGHT_TIMEOUT_S = 30.0
# Грабитель 4 минуты ищет жертву и 4 минуты грабит: клик, простоявший в очереди дольше, уже
# ничего не спасёт.
CLICK_TTL_S = 240.0
QUEUE_SIZE = 32


def fight_in(message_id: int) -> Predicate:
    """Итог — только правка того же сообщения на драку."""

    def predicate(delivery: Delivery) -> Match | None:
        if delivery.msg.msg_id != message_id:
            return None
        if any(isinstance(e, RobberyFight) for e in delivery.events):
            return Match(Verdict.CONFIRMED, RobberyFight.kind)
        return None

    return predicate


def wake_key(msg: IncomingMessage) -> str:
    return f"rob_awake:{msg.chat_id}:{msg.msg_id}"


def wake_button(msg: IncomingMessage) -> str | None:
    return next((b.data for b in msg.inline if b.data and WAKE_BUTTON.match(b.data)), None)


@dataclass(frozen=True, slots=True)
class _Alert:
    msg: IncomingMessage
    # Уже перечитана из Telegram (восстановленная из журнала при старте).
    verified: bool = False


class RobberyDefense:
    """Реакция на «тебя начал грабить»: срочный клик «Проснуться» по кнопке этого сообщения.

    Подписчик шины только ставит тревогу в очередь (шина ждёт подписчиков, а клик ждёт итога
    десятки секунд); клики шлёт `run` — задача под супервизором. На старте `run` поднимает из
    журнала тревоги прошлого процесса, на которые не успели кликнуть."""

    def __init__(
        self,
        *,
        gateway: ActionGateway,
        settings: SettingsProvider,
        reread: Reread | None = None,
        notifier: NotifierPort | None = None,
        alerts: Alerts | None = None,
        ready: Callable[[], bool] = lambda: True,
        clock: Clock | None = None,
        timeout_s: float = FIGHT_TIMEOUT_S,
        ttl_s: float = CLICK_TTL_S,
        ready_poll_s: float = 1.0,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._reread = reread
        self._notifier = notifier
        self._alerts = alerts
        self._ready = ready
        self._clock: Clock = clock or SystemClock()
        self._timeout_s = timeout_s
        self._ttl_s = ttl_s
        self._ready_poll_s = ready_poll_s
        self._queue: asyncio.Queue[_Alert] = asyncio.Queue(QUEUE_SIZE)
        self._queued: set[tuple[int, int]] = set()
        # Журнал прошлого процесса разбирается раз на процесс (перезапуск задачи — без повтора).
        self._recovering = False
        self.recovered = False

    async def on_delivery(self, delivery: Delivery) -> None:
        if not delivery.reactable or delivery.msg.outgoing:
            return
        if not any(isinstance(e, RobberyAlert) for e in delivery.events):
            return
        self._enqueue(_Alert(delivery.msg))

    def _enqueue(self, alert: _Alert) -> None:
        msg = alert.msg
        key = (msg.chat_id, msg.msg_id)
        if key in self._queued:
            return
        try:
            self._queue.put_nowait(alert)
        except asyncio.QueueFull:
            log.warning("robbery alert %s/%s dropped: queue full", msg.chat_id, msg.msg_id)
            return
        self._queued.add(key)

    async def run(self) -> None:
        if not self._recovering:
            self._recovering = True
            try:
                await self._recover()
            except Exception:
                log.exception("robbery alerts of previous process not recovered")
            self.recovered = True
        while True:
            alert = await self._queue.get()
            msg = alert.msg
            try:
                await self._react(alert)
            except Exception:
                log.exception("robbery alert %s/%s not handled", msg.chat_id, msg.msg_id)
            finally:
                self._queued.discard((msg.chat_id, msg.msg_id))

    async def _recover(self) -> None:
        """Тревоги, доставленные и записанные прошлым процессом, на которые клика не было: не
        старше `engine.recovered_react_max_age_min` и без итога в журнале. Каждую перечитываем —
        в Telegram она могла уже смениться на итог драки — и только затем ставим в очередь."""
        if self._alerts is None:
            return
        window = timedelta(minutes=self._settings.current.engine.recovered_react_max_age_min)
        started = self._clock.now()
        while not self._ready():
            if self._clock.now() - started > window:
                return
            await asyncio.sleep(self._ready_poll_s)
        journaled = await self._alerts(self._clock.now() - window)
        for msg in journaled:
            if wake_button(msg) is None:
                continue
            fresh = await self._reread(msg.chat_id, msg.msg_id) if self._reread else None
            if fresh is None:
                await self._warn(msg, "not reread after restart, wake click skipped")
                continue
            if wake_button(fresh) is not None:
                self._enqueue(_Alert(fresh, verified=True))

    async def _react(self, alert: _Alert) -> None:
        msg = alert.msg
        if not self._settings.current.features.robbery_defense:
            log.info("robbery alert %s ignored: robbery_defense off", msg.msg_id)
            return
        if msg.recovered and not alert.verified:
            # Тревога из догона: её могли уже прокликать с телефона или игра могла её переписать.
            fresh = await self._reread(msg.chat_id, msg.msg_id) if self._reread else None
            if fresh is None:
                await self._warn(msg, "not reread, wake click skipped")
                return
            msg = fresh
        button = wake_button(msg)
        if button is None:
            log.info("robbery alert %s already resolved", msg.msg_id)
            return
        result = await self._gateway.submit(
            ActionRequest(
                kind=ActionKind.CLICK,
                chat_id=msg.chat_id,
                message_id=msg.msg_id,
                data=button,
                source=Source.URGENT,
                expect=Expectation(fight_in(msg.msg_id), self._timeout_s),
                ttl_s=self._ttl_s,
                idempotency_key=wake_key(msg),
                expect_revision=msg.revision,
                expect_content=msg.content_hash(),
            )
        )
        if result.status is ActionStatus.CONFIRMED:
            log.info("robbery alert %s: woke up and fought", msg.msg_id)
            return
        await self._warn(msg, f"wake click {result.status.value} {result.reason}")

    async def _warn(self, msg: IncomingMessage, what: str) -> None:
        text = f"robbery alert {msg.msg_id}: {what}"
        log.warning(text)
        if self._notifier is not None:
            await self._notifier.notify("warn", "robbery_defense_failed", text)
