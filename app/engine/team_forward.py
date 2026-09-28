from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from app.engine.bus import Delivery
from app.engine.clock import Clock, SystemClock
from app.engine.events import Event
from app.engine.gametime import tasks_day
from app.engine.gateway.gateway import ActionGateway
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus, Source
from app.engine.notify import NotifierPort
from app.engine.parsing.crew import FactoryReport
from app.engine.parsing.daily import TaskCompleted
from app.engine.settings import SettingsProvider
from app.engine.types import IncomingMessage

log = logging.getLogger(__name__)
QUEUE_SIZE = 32
# Сколько последних отправленных (или с неясным исходом) ключей помнить: повтор той же доставки
# не доходит до шлюза и не даёт второго уведомления.
DONE_CAPACITY = 256
# Исходы, после которых ключ израсходован: повторная постановка вернула бы тот же итог.
SPENT = frozenset({ActionStatus.CONFIRMED, ActionStatus.OUTCOME_UNKNOWN, ActionStatus.REFUSED})
# Отказы по воле пользователя (сменил или выключил чат, остановил движок): без уведомления.
DELIBERATE = frozenset({"team_chat_off", "team_chat_changed", "kill_switch"})


def forward_key(msg: IncomingMessage, events: Sequence[Event]) -> str | None:
    """Ключ идемпотентности пересылки сообщения в чат команды; None — не пересылается.

    Итог задания — своё сообщение; отчёт о фабрике — только за сегодня (день битвы в отчёте —
    день создания сообщения по Москве), один на день: каждый `/fb` присылает его заново."""
    for event in events:
        if isinstance(event, TaskCompleted):
            return f"forward:{msg.chat_id}:{msg.msg_id}"
        if isinstance(event, FactoryReport) and event.battle_day == tasks_day(msg.origin):
            return f"forward:factory:{event.day}"
    return None


@dataclass(frozen=True, slots=True)
class _Item:
    msg: IncomingMessage
    key: str


class TeamForward:
    """Пересылка в чат команды (`chats.team_chat_id`) итога задания и сегодняшнего отчёта о
    фабрике (его запрашивает сценарий `factory_report` или ручной `/fb`).

    Подписчик шины только ставит пересылку в очередь; пересылает `run` — задача под супервизором.
    Пересылается только исходная ревизия (`revision == 0`) доставки, на которую можно реагировать,
    не старше `engine.recovered_react_max_age_min` от создания сообщения; возраст проверяется ещё
    раз перед отправкой. Шлюз шлёт её не больше одного раза (ключ `forward:…`), при неясном исходе
    без повтора — с уведомлением. Сбой процесса до постановки в очередь пересылку теряет."""

    def __init__(
        self,
        *,
        gateway: ActionGateway,
        settings: SettingsProvider,
        notifier: NotifierPort | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._notifier = notifier
        self._clock: Clock = clock or SystemClock()
        self._queue: asyncio.Queue[_Item] = asyncio.Queue(QUEUE_SIZE)
        self._queued: set[str] = set()
        self._done: OrderedDict[str, None] = OrderedDict()

    @property
    def queued(self) -> int:
        """Пересылки в очереди и в работе."""
        return len(self._queued)

    @property
    def idle(self) -> bool:
        return not self._queued

    async def on_delivery(self, delivery: Delivery) -> None:
        msg = delivery.msg
        if not delivery.reactable or msg.outgoing or msg.revision != 0:
            return
        chats = self._settings.current.chats
        if msg.chat_id != chats.game_chat_id or chats.team_chat_id is None:
            return
        key = forward_key(msg, delivery.events)
        if key is None or key in self._queued or key in self._done or self._left_s(msg) <= 0:
            return
        try:
            self._queue.put_nowait(_Item(msg, key))
        except asyncio.QueueFull:
            log.warning("team forward %s dropped: queue full", key)
            return
        self._queued.add(key)

    async def run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._forward(item)
            except Exception:
                log.exception("team forward %s failed", item.key)
            finally:
                self._queued.discard(item.key)

    async def _forward(self, item: _Item) -> None:
        msg = item.msg
        team = self._settings.current.chats.team_chat_id
        if team is None:
            log.info("team forward %s skipped: team chat off", item.key)
            return
        left = self._left_s(msg)
        if left <= 0:
            log.info("team forward %s skipped: message too old", item.key)
            return
        result = await self._gateway.submit(
            ActionRequest(
                kind=ActionKind.FORWARD,
                chat_id=team,
                from_chat_id=msg.chat_id,
                message_id=msg.msg_id,
                source=Source.PLANNER,
                ttl_s=left,
                idempotency_key=item.key,
            )
        )
        status, reason = result.status, result.reason
        if status in SPENT:
            self._done[item.key] = None
            while len(self._done) > DONE_CAPACITY:
                self._done.popitem(last=False)
        if status is ActionStatus.CONFIRMED:
            log.info("team forward %s sent as %s", item.key, result.answer)
        elif status is ActionStatus.OUTCOME_UNKNOWN:
            await self._warn(
                "team_forward_unknown",
                f"forward {msg.msg_id} to team chat: outcome unknown ({reason}), not retried",
            )
        elif status is ActionStatus.SUPPRESSED or reason in DELIBERATE:
            log.info("team forward %s not sent: %s %s", item.key, status.value, reason)
        else:
            await self._warn(
                "team_forward_failed",
                f"forward {msg.msg_id} to team chat {status.value}: {reason}",
            )

    def _left_s(self, msg: IncomingMessage) -> float:
        window = timedelta(minutes=self._settings.current.engine.recovered_react_max_age_min)
        return (window - (self._clock.now() - msg.origin)).total_seconds()

    async def _warn(self, code: str, text: str) -> None:
        log.warning(text)
        if self._notifier is not None:
            await self._notifier.notify("warn", code, text)
