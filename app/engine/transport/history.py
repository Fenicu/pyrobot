"""Сверка истории отслеживаемых чатов (раздел 4.3 спеки) — гарантия полноты журнала вместо
догонки kurigram (`skip_updates=True`): kurigram сохраняет `pts` раньше, чем сообщение дойдёт до
журнала, и не догоняет пропущенное после переподключения. Проход читает каждое чтение (чат,
отправитель) от сохранённой отметки и передаёт в конвейер то, чего нет в журнале; отметку
двигает только удачный проход, живые обновления — нет."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from app.engine.fence import LeaseLost
from app.engine.notify import NotifierPort
from app.engine.settings import ChatsSection
from app.engine.transport.base import FloodWait, TransportAuthLost
from app.engine.transport.kurigram import to_incoming
from app.engine.types import IncomingMessage, MessageKind

if TYPE_CHECKING:
    from app.db.chat_marks import ChatMarks

log = logging.getLogger(__name__)
# Чтение — (чат, отправитель); отправитель 0 — весь чат.
Reader = tuple[int, int]
# Ревизия сообщения в журнале: `msg_id`, `revision`, `content_hash`.
Key = tuple[int, int, str]


def readers_for(chats: ChatsSection) -> set[Reader]:
    """Чтения настроек аккаунта: чаты игры и смузи и чат приглашений к биржевикам — целиком,
    swinfo — по отправителю (фильтр берёт там сообщения одного пользователя, и сервер отдаёт
    только их). Чат приглашений, совпадающий с чатом swinfo, — второе чтение того же чата:
    сообщения swinfo не вытесняются из лимита чужими."""
    readers = {
        (chats.game_chat_id, 0),
        (chats.smoothie_channel_id, 0),
        (chats.swinfo_chat_id, chats.swinfo_user_id),
    }
    if chats.bulls_invite_chat_id is not None:
        readers.add((chats.bulls_invite_chat_id, 0))
    return readers


class HistorySource(Protocol):
    """История чтений в Telegram; сообщения — объекты kurigram, от новых к старым."""

    async def latest(self, reader: Reader) -> int | None:
        """`msg_id` самого нового сообщения чтения; None — сообщений нет."""
        ...

    async def read(self, reader: Reader, above: int, limit: int) -> list[Any]:
        """Самые новые сообщения с `id > above`, не больше `limit`."""
        ...

    async def tail(self, reader: Reader, upto: int, count: int) -> list[Any]:
        """`count` самых новых сообщений с `id <= upto`."""
        ...


class HistoryNotJournaled(Exception):
    """Переданное проходом не записано в журнал вовремя: отметка не двигается."""


def _key(msg: IncomingMessage) -> Key:
    return msg.msg_id, msg.revision, msg.content_hash()


class HistorySync:
    """Проходы сверки — задача аккаунта: по запросу (`request`: выход в онлайн, переподключение
    главной сессии, разрыв обновлений) и раз в `period_s` — от молчаливых пропусков. Запрос во
    время прохода — ещё один проход сразу после него. Пока `online()` ложно, проходов нет.
    Неудачный проход отметку не меняет и повторяется через `backoff`; запросы повтор не
    торопят — сбой мог быть FloodWait."""

    def __init__(
        self,
        source: HistorySource,
        marks: ChatMarks,
        known: Callable[[int, Sequence[Key]], Awaitable[set[Key]]],
        deliver: Callable[[list[IncomingMessage]], Awaitable[bool]],
        accepts: Callable[[Any], bool],
        notifier: NotifierPort,
        readers: set[Reader],
        online: Callable[[], bool],
        *,
        limit: int = 1000,
        tail: int = 50,
        period_s: float = 300.0,
        backoff: tuple[float, ...] = (30.0, 60.0, 120.0, 300.0),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._source = source
        self._marks = marks
        self._known = known
        self._deliver = deliver
        self._accepts = accepts
        self._notifier = notifier
        self._readers = readers
        self._online = online
        self._limit = limit
        self._tail = tail
        self._period = period_s
        self._backoff = backoff
        self._sleep = sleep
        self._wake = asyncio.Event()

    def request(self, reason: str) -> None:
        """Проход как можно скорее; не ждёт его (обработчик подключения kurigram выполняется
        внутри `Session.start`)."""
        log.debug("history pass requested: %s", reason)
        self._wake.set()

    async def run(self) -> None:
        failures = 0
        # Пауза до повтора неудачного прохода; None — ждать запроса или периода.
        retry: float | None = None
        while True:
            await (self._idle() if retry is None else self._sleep(retry))
            # Запросы, пришедшие до этого места, покрывает начинающийся проход.
            self._wake.clear()
            if not self._online():
                # Выход в онлайн сам запросит проход.
                failures, retry = 0, None
                continue
            try:
                await self.pass_once()
            except Exception:
                retry = self._backoff[min(failures, len(self._backoff) - 1)]
                failures += 1
                log.warning("history pass failed, retry in %.0fs", retry, exc_info=True)
            else:
                failures, retry = 0, None

    async def _idle(self) -> None:
        """До запроса или до периодического прохода."""
        if self._wake.is_set():
            return
        waiters = (
            asyncio.ensure_future(self._wake.wait()),
            asyncio.ensure_future(self._sleep(self._period)),
        )
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    async def pass_once(self) -> None:
        """Проход по всем чтениям. Сбой одного чтения (чат недоступен, пир не найден) остальные
        не останавливает: его отметка не меняется, а первый сбой пробрасывается после всех
        чтений — проход повторится по `backoff`. Сбой всего аккаунта (FloodWait, потеря аренды или
        входа) обрывает проход сразу: следующие чтения упёрлись бы в него же."""
        failure: Exception | None = None
        for reader in sorted(self._readers):
            try:
                await self._sync(reader)
            except (FloodWait, LeaseLost, TransportAuthLost):
                raise
            except Exception as exc:
                log.warning("history reader %s failed", reader, exc_info=True)
                failure = failure or exc
        if failure is not None:
            raise failure

    async def _sync(self, reader: Reader) -> None:
        chat_id = reader[0]
        mark = await self._marks.get(reader)
        if mark is None:
            # Чтение впервые: отметка — последнее сообщение, прошлое чата не читается.
            head = await self._source.latest(reader)
            if head is not None:
                await self._marks.advance(reader, head)
            return
        fresh = await self._source.read(reader, mark, self._limit + 1)
        if len(fresh) > self._limit:
            fresh = sorted(fresh, key=lambda m: int(m.id))[-self._limit :]
            await self._notifier.notify(
                "warn",
                "history_gap_truncated",
                f"chat {chat_id}: more than {self._limit} messages missed,"
                f" only the last {self._limit} recovered",
            )
        # На отметке и перед ней — только правки недавних сообщений: неправленое там уже в
        # журнале либо не читается вовсе (история до первой отметки чтения).
        recent = [m for m in await self._source.tail(reader, mark, self._tail) if m.edit_date]
        received = datetime.now(UTC)
        incoming: list[IncomingMessage] = []
        for m in sorted({int(m.id): m for m in (*recent, *fresh)}.values(), key=lambda m: m.id):
            try:
                if self._accepts(m):
                    kind: MessageKind = "edit" if m.edit_date else "new"
                    incoming.append(to_incoming(m, kind=kind, received_at=received))
            except Exception:
                log.exception("history message %s/%s not converted", chat_id, m.id)
        if incoming:
            known = await self._known(chat_id, [_key(msg) for msg in incoming])
            missing = [msg for msg in incoming if _key(msg) not in known]
            if missing:
                log.info("history pass: %d messages of chat %s recovered", len(missing), chat_id)
                if not await self._deliver(missing):
                    raise HistoryNotJournaled(f"chat {chat_id}: {len(missing)} messages")
        if fresh:
            await self._marks.advance(reader, max(int(m.id) for m in fresh))
