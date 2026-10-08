from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.engine.bus import Delivery
from app.engine.clock import Clock, SystemClock
from app.engine.gateway.gateway import BULLS_WALK, ActionGateway
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
from app.engine.notify import Level, NotifierPort
from app.engine.parsing.bulls import BullsEncounter
from app.engine.planner.obligations import metro_inside, night_start
from app.engine.settings import SettingsProvider
from app.engine.state.model import CharacterState
from app.engine.types import IncomingMessage

log = logging.getLogger(__name__)
QUEUE_SIZE = 8
DONE_CAPACITY = 64
# На раздумья игра даёт 3 минуты; клик, который уйдёт позже, бесполезен — с запасом на доставку.
OFFER_WINDOW_S = 180
OFFER_MARGIN_S = 5.0
# Ответ на «⚔Драться» и следующие за ним сообщения, среди которых ищем приглашение.
ANSWER_TIMEOUT_S = 30.0
FOLLOW_S = 15.0
INVITE_TTL_S = 60.0
FIGHT_ACCEPT = "fight_accept"
_CODE_IN_TEXT = re.compile(r"join_fight_[A-Za-z0-9_-]{11}(?![A-Za-z0-9_-])")
HEAD_LEN = 160


def answers_offer(offer: IncomingMessage, msg: IncomingMessage) -> bool:
    """Ответ игры на клик по встрече: правка самой встречи или новое сообщение после неё."""
    if msg.chat_id != offer.chat_id or msg.outgoing:
        return False
    if msg.msg_id == offer.msg_id:
        return msg.content_hash() != offer.content_hash()
    return msg.msg_id > offer.msg_id


def share_query(msg: IncomingMessage) -> str | None:
    """Запрос инлайн-режима, которым игра предлагает позвать друзей: кнопка переключения в
    инлайн-режим (в любой чат или с выбором чата) или код `join_fight_…` в тексте и кнопке
    копирования. None — приглашения в сообщении нет."""
    for button in msg.inline:
        query = (button.switch_chosen or button.switch or "").strip()
        if query:
            return query
    for source in (msg.text, *(b.copy for b in msg.inline)):
        if source and (m := _CODE_IN_TEXT.search(source)):
            return m[0]
    return None


def blocked_by_state(state: CharacterState, now: datetime) -> str | None:
    """Почему персонажу сейчас не до драки: в метро, занят не прогулкой (сон, биржевики, дело),
    уже побеждал биржевиков этой ночью. Прогулка, которая как раз кончается встречей, не мешает."""
    if metro_inside(state, now) is not None:
        return "metro"
    busy = state.busy
    if busy is not None and busy.src != "doubtful" and busy.value is not None:
        if busy.value.until > now and busy.value.activity != "walk":
            return f"busy:{busy.value.activity}"
    won = state.bulls_won_at
    if won is not None and won.value >= night_start(now):
        return "won_tonight"
    return None


def _head(text: str | None) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= HEAD_LEN else flat[: HEAD_LEN - 1] + "…"


@dataclass(eq=False)
class _Watch:
    """Сообщения игры после клика «⚔Драться», по одному на ревизию."""

    offer: IncomingMessage
    seen: dict[tuple[int, int, str], IncomingMessage] = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def feed(self, msg: IncomingMessage) -> None:
        if not answers_offer(self.offer, msg):
            return
        self.seen.setdefault((msg.msg_id, msg.revision, msg.content_hash()), msg)
        self.changed.set()

    def share(self) -> str | None:
        return next((q for m in self.seen.values() if (q := share_query(m)) is not None), None)


@dataclass(frozen=True, slots=True)
class _Offer:
    msg: IncomingMessage
    enemy: str
    window_s: int


class BullsWalk:
    """Реакция на встречу с 🐮Быком / 🐻Медведем на ночной прогулке: если чат приглашений задан
    и персонаж свободен, один раз жмёт «⚔Драться» и зовёт в чат приглашений так, как это сделал
    бы человек, — результатом инлайн-режима бота игры по запросу из ответа на клик.

    Экран после «⚔Драться» ещё не видели: приглашение ищется в правке встречи и следующих за ней
    сообщениях (кнопка переключения в инлайн-режим или код `join_fight_…`). Не нашлось — больше
    ничего не жмёт: предупреждение `bulls_walk_unknown` с началом ответа (сам ответ — в
    нераспознанных). Подписчик шины ставит встречу в очередь, кликает `run` — задача под
    супервизором. Встречи прошлого процесса не поднимаются: на раздумья всего 3 минуты."""

    def __init__(
        self,
        *,
        gateway: ActionGateway,
        settings: SettingsProvider,
        state: Callable[[], CharacterState],
        notifier: NotifierPort | None = None,
        clock: Clock | None = None,
        answer_timeout_s: float = ANSWER_TIMEOUT_S,
        follow_s: float = FOLLOW_S,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._state = state
        self._notifier = notifier
        self._clock: Clock = clock or SystemClock()
        self._answer_timeout_s = answer_timeout_s
        self._follow_s = follow_s
        self._queue: asyncio.Queue[_Offer] = asyncio.Queue(QUEUE_SIZE)
        self._queued: set[int] = set()
        self._done: OrderedDict[int, None] = OrderedDict()
        self._watch: _Watch | None = None

    @property
    def idle(self) -> bool:
        return not self._queued

    async def on_delivery(self, delivery: Delivery) -> None:
        msg = delivery.msg
        if msg.chat_id != self._settings.current.chats.game_chat_id:
            return
        watch = self._watch
        if watch is not None:
            watch.feed(msg)
        if not delivery.reactable or msg.outgoing or msg.revision != 0:
            return
        encounter = next((e for e in delivery.events if isinstance(e, BullsEncounter)), None)
        if encounter is None or msg.msg_id in self._queued or msg.msg_id in self._done:
            return
        item = _Offer(msg, encounter.enemy, encounter.expires_in_s or OFFER_WINDOW_S)
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            log.warning("bulls walk offer %s dropped: queue full", msg.msg_id)
            return
        self._queued.add(msg.msg_id)

    async def run(self) -> None:
        while True:
            item = await self._queue.get()
            msg_id = item.msg.msg_id
            try:
                await self._handle(item)
            except Exception:
                log.exception("bulls walk offer %s not handled", msg_id)
            finally:
                self._watch = None
                self._done[msg_id] = None
                while len(self._done) > DONE_CAPACITY:
                    self._done.popitem(last=False)
                self._queued.discard(msg_id)

    async def _handle(self, item: _Offer) -> None:
        msg = item.msg
        current = self._settings.current
        invites = current.chats.bulls_invite_chat_id
        now = self._clock.now()
        skip: str | None = None
        if not current.features.bulls:
            skip = "bulls off"
        elif invites is None:
            skip = "no invite chat"
        elif current.engine.paused:
            skip = "engine paused"
        else:
            skip = blocked_by_state(self._state(), now)
        left = item.window_s - OFFER_MARGIN_S - (now - msg.origin).total_seconds()
        if skip is None and left <= 0:
            skip = "offer expired"
        if skip is not None:
            log.info("bulls walk offer %s skipped: %s", msg.msg_id, skip)
            return
        watch = _Watch(msg)
        self._watch = watch
        result = await self._gateway.submit(
            ActionRequest(
                kind=ActionKind.CLICK,
                chat_id=msg.chat_id,
                message_id=msg.msg_id,
                data=FIGHT_ACCEPT,
                source=Source.URGENT,
                scenario=BULLS_WALK,
                expect=Expectation(_answered(watch), self._answer_timeout_s),
                ttl_s=left,
                idempotency_key=f"bulls_walk:{msg.chat_id}:{msg.msg_id}",
                expect_revision=msg.revision,
                expect_content=msg.content_hash(),
            )
        )
        if result.status in (ActionStatus.SUPPRESSED, ActionStatus.REJECTED):
            log.info(
                "bulls walk offer %s: fight not accepted (%s %s)",
                msg.msg_id,
                result.status.value,
                result.reason,
            )
            return
        if result.status is not ActionStatus.CONFIRMED:
            await self._unknown(
                msg, watch, f"fight click {result.status.value} {result.reason}", result.answer
            )
            return
        query = await self._share(watch)
        if query is None:
            await self._unknown(msg, watch, "no invite in answer", result.answer)
            return
        await self._invite(item, invites, query, watch)

    async def _share(self, watch: _Watch) -> str | None:
        """Приглашение в ответе на клик; нет в первом — ждём следующие сообщения игры."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._follow_s
        while (query := watch.share()) is None:
            left = deadline - loop.time()
            if left <= 0:
                return None
            watch.changed.clear()
            try:
                await asyncio.wait_for(watch.changed.wait(), left)
            except TimeoutError:
                return watch.share()
        return query

    async def _invite(self, item: _Offer, invites: int | None, query: str, watch: _Watch) -> None:
        msg = item.msg
        if invites is None:
            return
        result = await self._gateway.submit(
            ActionRequest(
                kind=ActionKind.INLINE,
                chat_id=invites,
                text=query,
                source=Source.URGENT,
                scenario=BULLS_WALK,
                ttl_s=INVITE_TTL_S,
                idempotency_key=f"bulls_share:{msg.msg_id}",
            )
        )
        if result.status is ActionStatus.CONFIRMED:
            text = (
                f"bulls walk {item.enemy} {msg.msg_id}: fight accepted, invite {query} posted "
                f"to chat {invites} as {result.answer}"
            )
            log.info(text)
            await self._notify("info", "bulls_walk_invited", text)
            return
        await self._unknown(
            msg, watch, f"fight accepted, invite {query} {result.status.value} {result.reason}"
        )

    async def _unknown(
        self, msg: IncomingMessage, watch: _Watch, what: str, toast: str | None = None
    ) -> None:
        heads = [_head(m.text) for m in watch.seen.values()]
        if toast:
            heads.insert(0, f"toast: {_head(toast)}")
        text = f"bulls walk offer {msg.msg_id}: {what}; answer: {' | '.join(heads) or '-'}"
        log.warning(text)
        await self._notify("warn", "bulls_walk_unknown", text)

    async def _notify(self, level: Level, code: str, text: str) -> None:
        if self._notifier is not None:
            await self._notifier.notify(level, code, text)


def _answered(watch: _Watch) -> Predicate:
    """Любой ответ игры на клик подтверждает его: что это за экран, разбирает реакция."""

    def predicate(delivery: Delivery) -> Match | None:
        if not answers_offer(watch.offer, delivery.msg):
            return None
        watch.feed(delivery.msg)
        return Match(Verdict.CONFIRMED, "answered")

    return predicate
