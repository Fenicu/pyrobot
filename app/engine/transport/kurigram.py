from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Concatenate

from app.engine.fence import LeaseLost
from app.engine.parsing.bulls import INVITE_CODE
from app.engine.settings import ChatsSection
from app.engine.tg_auth import (
    CodeExpired,
    InvalidCode,
    InvalidPassword,
    InvalidPhone,
    PasswordRequired,
    SendCodeRejected,
    SignUpRequired,
)
from app.engine.transport.base import (
    ChatUnavailable,
    FloodWait,
    GroupInfo,
    JoinStatus,
    TransportAuthLost,
    TransportRejected,
)
from app.engine.types import Button, IncomingMessage, MessageKind

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.db.base import Database
    from app.db.crypto import SecretBox
    from app.db.tg_storage import PgSessionStorage
    from app.engine.fence import Fence
    from app.engine.transport.history import Reader

log = logging.getLogger(__name__)
Sink = Callable[[IncomingMessage], Awaitable[None]]
DIALOGS_WARMUP = 200
# Сообщений истории за один запрос: больше Telegram не отдаёт.
HISTORY_PAGE = 100
# Перегрузка (раздел 4.2 спеки): очередь kurigram и очередь конвейера вместе выше OVERLOAD_HIGH —
# приём останавливается; ниже OVERLOAD_LOW (проверка раз в OVERLOAD_CHECK_S) — новый клиент.
# Не подключился — повтор через OVERLOAD_RETRY_S, дальше раз в последнюю паузу.
OVERLOAD_HIGH = 5000
OVERLOAD_LOW = 500
OVERLOAD_CHECK_S = 1.0
OVERLOAD_RETRY_S = (5.0, 30.0, 60.0)
# Устройство сессии в списке сессий пользователя в Telegram — у клиента движка и у временного
# клиента выхода одинаковое.
_DEVICE: dict[str, Any] = {
    "sleep_threshold": 10,
    "device_model": "pyrobot",
    "app_version": "2.0",
    "system_version": "Linux",
    "lang_code": "ru",
}


def _aware(dt: datetime) -> datetime:
    return (dt.astimezone() if dt.tzinfo is None else dt).astimezone(UTC)


def _buttons(markup: Any) -> tuple[tuple[Button, ...], tuple[tuple[str, ...], ...]]:
    if markup is None:
        return (), ()
    if getattr(markup, "inline_keyboard", None) is not None:
        inline: list[Button] = []
        for r, row in enumerate(markup.inline_keyboard):
            for c, b in enumerate(row):
                data = b.callback_data
                if isinstance(data, bytes):
                    data = data.decode("utf-8", "replace")
                switch = b.switch_inline_query or b.switch_inline_query_current_chat
                inline.append(Button(b.text, r, c, data=data, url=b.url, switch=switch))
        return tuple(inline), ()
    if getattr(markup, "keyboard", None) is not None:
        kb = tuple(
            tuple(b if isinstance(b, str) else b.text for b in row) for row in markup.keyboard
        )
        return (), kb
    return (), ()


def has_join_fight(m: Any) -> bool:
    inline, _ = _buttons(m.reply_markup)
    return any(
        INVITE_CODE.match(b.switch or "") or INVITE_CODE.match(b.data or "") for b in inline
    )


def to_incoming(
    m: Any,
    *,
    kind: MessageKind,
    received_at: datetime,
    recovered_after: timedelta = timedelta(seconds=60),
) -> IncomingMessage:
    inline, reply_kb = _buttons(m.reply_markup)
    # Догон истории отдаёт уже правленое сообщение как новое: время события — время правки.
    date = _aware(m.edit_date or m.date)
    received = _aware(received_at)
    text = m.text or m.caption
    return IncomingMessage(
        chat_id=m.chat.id,
        msg_id=m.id,
        revision=int(date.timestamp()) if m.edit_date or kind == "edit" else 0,
        kind=kind,
        date=date,
        received_at=received,
        text=str(text) if text else None,
        inline=inline,
        reply_kb=reply_kb,
        from_id=m.from_user.id if m.from_user else None,
        outgoing=bool(m.outgoing),
        recovered=(received - date) > recovered_after,
        created_at=_aware(m.date),
    )


def _forwarded_id(updates: Any, random_id: int) -> int:
    """Id пересланной копии: по `UpdateMessageID` своего `random_id` или из нового сообщения."""
    from pyrogram import raw

    found = 0
    for update in getattr(updates, "updates", None) or ():
        if isinstance(update, raw.types.UpdateMessageID) and update.random_id == random_id:
            return int(update.id)
        if isinstance(update, raw.types.UpdateNewMessage | raw.types.UpdateNewChannelMessage):
            found = found or int(getattr(update.message, "id", 0) or 0)
    return found


@dataclass(frozen=True)
class ChatFilter:
    game_chat_id: int
    swinfo_chat_id: int
    swinfo_user_id: int
    smoothie_channel_id: int | None
    bulls_chat_id: int | None

    @classmethod
    def from_settings(cls, chats: ChatsSection) -> ChatFilter:
        return cls(
            chats.game_chat_id,
            chats.swinfo_chat_id,
            chats.swinfo_user_id,
            chats.smoothie_channel_id,
            chats.bulls_invite_chat_id,
        )

    def accepts(self, m: Any) -> bool:
        chat = m.chat.id
        if chat == self.game_chat_id or chat == self.smoothie_channel_id:
            return True
        # Чат инвайтов может совпадать с общим чатом SWINFO: правила проверяются независимо.
        if chat == self.swinfo_chat_id and m.from_user and m.from_user.id == self.swinfo_user_id:
            return True
        return self.bulls_chat_id is not None and chat == self.bulls_chat_id and has_join_fight(m)


def session_peers(chats: ChatsSection) -> set[int]:
    """Пиры, которые хранилище сессии держит в базе (раздел 4.3 спеки): чаты из настроек и
    пользователь swinfo — поиску по отправителю нужен его `access_hash`, а kurigram не сохраняет
    min-пользователей из обновлений супергрупп."""
    peers = {
        chats.game_chat_id,
        chats.swinfo_chat_id,
        chats.swinfo_user_id,
        chats.smoothie_channel_id,
        chats.tangerine_chat_id,
        chats.bulls_invite_chat_id,
        chats.team_chat_id,
    }
    return {peer for peer in peers if peer is not None}


async def _force_close(client: Any) -> None:
    # watchdog обновлений kurigram, умерший на отозванной сессии, перевыбрасывает
    # Unauthorized внутри terminate() ДО is_initialized=False — stop() пропускает
    # disconnect(), и утекает MTProto-сессия. Форсируем закрытие каждым шагом отдельно,
    # чтобы ни один ресурс не остался открытым.
    try:
        if client.is_initialized:
            await client.stop()
        elif client.is_connected:
            await client.disconnect()
        return
    except Exception:
        log.warning("telegram client stop failed, forcing disconnect", exc_info=True)
    with suppress(Exception):
        client.is_initialized = False
    with suppress(Exception):
        if client.is_connected:
            await client.disconnect()
    with suppress(Exception):
        await client.storage.close()


async def _stop_session(session: Any) -> None:
    with suppress(Exception):
        await session.stop()


def _too_long(updates: Any) -> bool:
    """Telegram сообщает о разрыве обновлений: `UpdatesTooLong` или `UpdateChannelTooLong` — в
    пачке обновлений или одиночным `UpdateShort`."""
    from pyrogram import raw

    if isinstance(updates, raw.types.UpdatesTooLong):
        return True
    inner = [*getattr(updates, "updates", ()), getattr(updates, "update", None)]
    return any(isinstance(update, raw.types.UpdateChannelTooLong) for update in inner)


@functools.cache
def _client_class() -> type[Any]:
    """Клиент kurigram, который сообщает транспорту (`history_needed`) о разрывах обновлений.
    `UpdatesTooLong`, `UpdateChannelTooLong` и ошибку внутри `handle_updates` (например,
    `GetDifference` для `UpdateShortMessage` — так приходят личные сообщения игрового бота)
    kurigram только пишет в лог, а своя догонка выключена: пропущенное вернёт сверка истории."""
    from pyrogram import Client

    class GapAwareClient(Client):
        history_needed: Callable[[str], None] | None = None

        def _need_history(self, reason: str) -> None:
            if self.history_needed is not None:
                self.history_needed(reason)

        async def handle_updates(self, updates: Any) -> None:
            if _too_long(updates):
                self._need_history("gap")
            try:
                await super().handle_updates(updates)  # type: ignore[no-untyped-call]
            except Exception:
                self._need_history("handle_updates")
                raise

    return GapAwareClient


class CountingQueue(asyncio.Queue[Any]):
    """Очередь обновлений диспетчера kurigram (`dispatcher.updates_queue`), которая сообщает
    транспорту о каждом принятом обновлении (`on_put`), — по ней транспорт считает перегрузку.
    Размер не ограничен: сетевой слой kurigram кладёт через `put_nowait` и ждать места не умеет."""

    def __init__(self, on_put: Callable[[], None]) -> None:
        super().__init__()
        self._on_put = on_put

    def put_nowait(self, item: Any) -> None:
        super().put_nowait(item)
        # None — знак остановки обработчиков (`Dispatcher.stop`), а не обновление.
        if item is not None:
            self._on_put()


def _fenced[**P, T](
    method: Callable[Concatenate[KurigramTransport, P], Awaitable[T]],
) -> Callable[Concatenate[KurigramTransport, P], Coroutine[Any, Any, T]]:
    """Вызов Telegram — через ограду аренды аккаунта: не начинается после её местного срока и
    обрывается на нём (`Fence.call`)."""

    @functools.wraps(method)
    async def fenced(self: KurigramTransport, /, *args: P.args, **kwargs: P.kwargs) -> T:
        return await self._fence.call(lambda: method(self, *args, **kwargs))

    return fenced


class KurigramTransport:
    """Клиент kurigram аккаунта на сессии из базы (`PgSessionStorage` — одно хранилище на все
    клиенты транспорта). Своя догонка kurigram выключена (`skip_updates=True`): пропущенное
    возвращает сверка истории — транспорт её источник (`latest`, `read`, `tail`) и просит
    проход (`on_history_needed`) после выхода в онлайн, при переподключении главной сессии и
    разрыве обновлений. Все вызовы Telegram идут через ограду аренды `fence`. Принятое, но не
    записанное — очередь kurigram и очередь конвейера (`backlog`) — ограничено перегрузкой
    (`on_overload`, `on_resumed`)."""

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        account_id: int,
        storage: PgSessionStorage,
        fence: Fence,
        chat_filter: ChatFilter,
        sink: Sink,
        backlog: Callable[[], int],
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._account_id = account_id
        self._storage = storage
        self._fence = fence
        self._filter = chat_filter
        self._sink = sink
        self._backlog = backlog
        # Аварийная остановка началась: обновления в конвейер больше не передаются.
        self._aborted = False
        # Перегрузка — три задачи: остановка сессии, остановка приёма (она же разбор принятого)
        # и вся перегрузка до возврата клиента; None — приём идёт.
        self._session_stop: asyncio.Task[None] | None = None
        self._shedding: asyncio.Task[None] | None = None
        self._overload: asyncio.Task[None] | None = None
        self.overload_check_s = OVERLOAD_CHECK_S
        self.overload_retry_s = OVERLOAD_RETRY_S
        self.on_auth_lost: Callable[[], Awaitable[None]] | None = None
        # Приём остановлен перегрузкой; клиент после неё снова онлайн.
        self.on_overload: Callable[[], Awaitable[None]] | None = None
        self.on_resumed: Callable[[], Awaitable[None]] | None = None
        # Проход сверки истории — с поводом; обработчик только ставит его в очередь.
        self.on_history_needed: Callable[[str], None] | None = None
        # Клиент вошёл, прошёл проверку привязки и вышел в онлайн (`go_online`).
        self._online = False
        self._me: Any = None
        # Peer чатов, разрешённые заранее (`resolve`), — для текущего клиента.
        self._peers: dict[int, Any] = {}
        self._client = self._new_client()

    @property
    def online(self) -> bool:
        return self._online

    def _new_client(self) -> Any:
        client = self._make_client()
        client.dispatcher.updates_queue = CountingQueue(lambda: self._queued(client))
        return client

    def _make_client(self) -> Any:
        from pyrogram.handlers import ConnectHandler, EditedMessageHandler, MessageHandler

        client = _client_class()(
            f"account-{self._account_id}",
            api_id=self._api_id,
            api_hash=self._api_hash,
            storage_engine=self._storage,
            workers=1,
            skip_updates=True,
            **_DEVICE,
        )
        client.history_needed = self._history_needed
        client.add_handler(MessageHandler(self._on_new))
        client.add_handler(EditedMessageHandler(self._on_edit))
        client.add_handler(ConnectHandler(self._on_connect))
        return client

    def _history_needed(self, reason: str) -> None:
        if self.on_history_needed is not None:
            self.on_history_needed(reason)

    def _pending(self, client: Any) -> int:
        """Принятое, но ещё не записанное: очередь kurigram клиента и очередь конвейера."""
        return int(client.dispatcher.updates_queue.qsize()) + self._backlog()

    def _queued(self, client: Any) -> None:
        # Считается только онлайн-клиент: до `initialize()` обработчиков нет, очередь разбирать
        # некому; при аварийной остановке приёма уже нет.
        if not self._online or client is not self._client or self._overload is not None:
            return
        pending = self._pending(client)
        if pending > OVERLOAD_HIGH:
            log.warning("telegram updates overload: %d pending, intake stopped", pending)
            self._online = False
            stopping = None
            if client.session is not None:
                stopping = asyncio.create_task(_stop_session(client.session))
            self._session_stop = stopping
            self._shedding = asyncio.create_task(self._shed(client, stopping))
            self._overload = asyncio.create_task(self._ride_out_overload(client, self._shedding))

    @staticmethod
    async def _shed(client: Any, stopping: asyncio.Task[None] | None) -> None:
        """Остановка приёма при перегрузке (раздел 4.2 спеки): сессия перестаёт принимать,
        `terminate()` — диспетчер разбирает принятое в конвейер, `disconnect()` закрывает
        хранилище, не удаляя его; выхода из Telegram нет. Штатная остановка и выход её не
        отменяют, а ждут (`_end_overload`): отмена посреди `terminate()` оборвала бы обработчики
        диспетчера на полпути."""
        if stopping is not None:
            await asyncio.shield(stopping)
        # terminate() и disconnect(), без утечки сессии при сбое одного из них.
        await _force_close(client)

    async def _ride_out_overload(self, client: Any, shedding: asyncio.Task[None]) -> None:
        """Перегрузка: статус и остановка приёма, затем, когда накопленное разобрано ниже
        `OVERLOAD_LOW`, — новый клиент; не подключился — повтор через `overload_retry_s`, статус
        остаётся перегрузкой. Своей догонки у kurigram нет, поэтому пропущенное за перегрузку
        вернёт сверка истории, а не поток, который снова перегрузит."""
        try:
            # Статус — сразу, вместе с остановкой приёма: остановка сессии может идти секунды
            # (ждёт задачи `handle_updates`), а шлюз не должен слать в неё; уведомление её не
            # задерживает.
            await asyncio.gather(asyncio.shield(shedding), self._report(self.on_overload))
            # Накопленное считается по остановленному клиенту: его очередь разобрал terminate(),
            # а очередь клиента, не вышедшего в онлайн, разбирать некому.
            stopped, failures = client, 0
            while True:
                # Опрос, а не событие: конвейер о разборе очереди не сообщает.
                while self._pending(stopped) >= OVERLOAD_LOW:  # noqa: ASYNC110
                    await asyncio.sleep(self.overload_check_s)
                if await self._resume(client):
                    return
                retry = self.overload_retry_s
                delay = retry[min(failures, len(retry) - 1)]
                failures += 1
                log.info("telegram client back after overload: retry in %.0fs", delay)
                await asyncio.sleep(delay)
                client = self._client
        finally:
            self._overload = None

    async def _resume(self, previous: Any) -> bool:
        """Новый объект клиента на том же хранилище — сессия kurigram одноразовая, а `terminate()`
        снял обработчики, — `connect()` и `go_online()` через ограду аренды. Входа заново и
        проверки привязки нет: сессия в хранилище та же. Выход в онлайн просит проход сверки.
        False — сбой, после которого стоит повторить (новый клиент закрыт)."""
        from pyrogram import errors

        if self._client is not previous:
            # За время перегрузки клиент сброшен (выход, потеря входа) — возвращать нечего.
            return True
        client = self._client = self._new_client()
        self._peers = {}
        try:
            if not await self.connect():
                raise TransportAuthLost("session is not authorized")
            await self.go_online()
        except LeaseLost:
            # Аренда потеряна: движок останавливается аварийно.
            return True
        except (TransportAuthLost, errors.Unauthorized):
            # connect() при отозванной сессии уже сбросил клиент.
            await self._reset_client(client)
            await self._auth_lost()
            return True
        except Exception:
            if self._client is not client:
                # Клиент сброшен выходом во время подключения.
                return True
            log.warning("telegram client not back online after overload", exc_info=True)
            await _force_close(client)
            return False
        log.info("telegram client back online after overload")
        await self._report(self.on_resumed)
        return True

    @staticmethod
    async def _report(callback: Callable[[], Awaitable[None]] | None) -> None:
        if callback is not None:
            try:
                await callback()
            except Exception:
                log.exception("overload callback failed")

    async def _end_overload(self, *, hard: bool = False) -> None:
        """Снимает перегрузку: ожидание разбора и возврат клиента отменяются, а остановка приёма
        доводится до конца — `terminate()` разбирает принятое. Аварийная остановка (`hard`)
        обрывает и её, кроме остановки сессии: повторный `stop()` останавливающейся сессии ничего
        не делает, и хранилище закрылось бы раньше её задач `handle_updates`."""
        overload, shedding, stopping = self._overload, self._shedding, self._session_stop
        cancelled = [task for task in (overload, shedding if hard else None) if task is not None]
        for task in cancelled:
            task.cancel()
        if cancelled:
            await asyncio.gather(*cancelled, return_exceptions=True)
        # wait, а не gather: отмена ждущего (остановки движка) их не обрывает.
        rest = {task for task in (shedding, stopping) if task is not None and not task.done()}
        if rest:
            await asyncio.wait(rest)
        # Задача, отменённая до первого шага, свой finally не выполняет.
        self._overload = self._shedding = self._session_stop = None

    async def _on_connect(self, client: Any, session: Any) -> None:
        # Переподключение главной сессии онлайн-клиента: пропущенное за обрыв вернёт сверка.
        # При первом connect() `client.session` ещё не присвоена — его покрывает go_online;
        # сессии других DC и медиа не в счёт. kurigram ждёт обработчик внутри Session.start.
        if self._online and client is self._client and session is client.session:
            self._history_needed("reconnect")

    async def _on_new(self, _client: Any, message: Any) -> None:
        await self._forward(message, "new")

    async def _on_edit(self, _client: Any, message: Any) -> None:
        await self._forward(message, "edit")

    async def _forward(self, message: Any, kind: MessageKind) -> None:
        if self._aborted:
            return
        try:
            if not self._filter.accepts(message):
                return
            await self._sink(to_incoming(message, kind=kind, received_at=datetime.now(UTC)))
        except Exception:
            log.exception("update not forwarded")

    async def _auth_lost(self) -> None:
        if self.on_auth_lost is not None:
            try:
                await self.on_auth_lost()
            except Exception:
                log.exception("auth lost callback failed")

    async def _reset_client(self, client: Any) -> bool:
        # Подмена до остановки: параллельный 401 старого клиента не сбросит сессию
        # повторно; новый клиент не открывает хранилище до connect().
        if self._client is not client:
            return False
        self._online = False
        self._me = None
        self._peers = {}
        self._client = self._new_client()
        await _force_close(client)
        try:
            await self._storage.delete()
        except Exception:
            log.exception("telegram session storage not deleted")
        return True

    async def _lose_auth(self, client: Any) -> None:
        if await self._reset_client(client):
            await self._auth_lost()

    @_fenced
    async def connect(self) -> bool:
        from pyrogram import errors

        client = self._client
        try:
            if not client.is_connected:
                return bool(await client.connect())
            return (await client.storage.user_id()) is not None
        except errors.Unauthorized as exc:
            await self._reset_client(client)
            raise TransportAuthLost(str(exc)) from exc

    @_fenced
    async def send_code(self, phone: str) -> str:
        from pyrogram import errors

        # kurigram 2.2.x: метод называется send_phone_number_code (переименован из send_code).
        try:
            sent = await self._client.send_phone_number_code(phone)
        except errors.PhoneNumberInvalid as exc:
            raise InvalidPhone from exc
        except errors.FloodWait as exc:
            raise FloodWait(float(exc.seconds or 0)) from exc
        except errors.BadRequest as exc:
            raise SendCodeRejected(str(exc.ID or exc).lower()) from exc
        return str(sent.phone_code_hash)

    @_fenced
    async def sign_in(self, phone: str, code_hash: str, code: str) -> int:
        from pyrogram import errors, types

        try:
            user = await self._client.sign_in(phone, code_hash, code)
        except errors.SessionPasswordNeeded as exc:
            raise PasswordRequired from exc
        except errors.PhoneCodeInvalid as exc:
            raise InvalidCode from exc
        except errors.PhoneCodeExpired as exc:
            raise CodeExpired from exc
        if not isinstance(user, types.User):
            raise SignUpRequired
        return int(user.id)

    @_fenced
    async def check_password(self, password: str) -> int:
        from pyrogram import errors

        try:
            user = await self._client.check_password(password)
        except errors.PasswordHashInvalid as exc:
            raise InvalidPassword from exc
        return int(user.id)

    @_fenced
    async def identify(self) -> int:
        from pyrogram import errors

        client = self._client
        try:
            self._me = await client.get_me()
        except errors.Unauthorized as exc:
            # Сброс, а не _lose_auth: identify() зовётся из TgAuthManager.boot() под его
            # локом, а on_auth_lost обычно привязан к mark_lost(), который тот же лок
            # захватывает повторно — дедлок. boot() сам решает, что делать с потерей.
            await self._reset_client(client)
            raise TransportAuthLost(str(exc)) from exc
        return int(self._me.id)

    @_fenced
    async def go_online(self) -> None:
        from pyrogram import raw
        from pyrogram.storage import UpdateState

        state = await self._client.invoke(raw.functions.updates.GetState())
        if not await self._client.storage.get_update_states(0):
            await self._client.storage.set_update_state(
                UpdateState(0, state.pts, state.qts, state.date, state.seq)
            )
            await self._client.storage.save()
        # identity из identify() (boot) переиспользуется, при входе по коду get_me — один раз.
        me, self._me = self._me, None
        self._client.me = me if me is not None else await self._client.get_me()
        async for _ in self._client.get_dialogs(limit=DIALOGS_WARMUP):
            pass
        await self._client.initialize()
        self._online = True
        self._history_needed("online")

    async def disconnect(self) -> None:
        """Отключение без выхода (отказ в онлайне: свой чат в настройках, сбой привязки, раздел
        4.3 спеки): клиент, не вышедший в онлайн, не копит обновления, которые некому разбирать.
        Сессия остаётся в хранилище, следующий `connect()` или выход — новым клиентом на нём
        (сессия kurigram одноразовая)."""
        client = self._client
        self._online = False
        self._me = None
        self._peers = {}
        self._client = self._new_client()
        await _force_close(client)

    @_fenced
    async def log_out(self) -> None:
        from pyrogram import errors, raw

        failure: Exception | None = None
        if self._overload is not None:
            # Перегрузка: клиент отключён, а выход закрывает сессию и у Telegram (удаление
            # аккаунта, раздел 4.2 спеки). Возврат клиента снимается.
            await self._end_overload()
            await _force_close(self._client)
            self._client = self._new_client()
        client = self._client
        if not client.is_connected and await self._storage.user_id() is not None:
            # Клиент не подключён (перегрузка, отключение без выхода), а сессия — в хранилище:
            # он подключается только для `auth.LogOut`.
            try:
                await client.connect()
            except Exception as exc:
                failure = exc
        if failure is None and client.is_connected:
            try:
                await client.invoke(
                    raw.functions.auth.LogOut(), retries=1, sleep_threshold=0, retry_delay=0
                )
            except errors.Unauthorized:
                log.info("session already revoked, logging out locally")
            except Exception as exc:
                failure = exc
        await self._reset_client(client)
        if failure is not None:
            raise failure

    @_fenced
    async def probe(self) -> None:
        from pyrogram import errors, raw

        client = self._client
        try:
            await client.invoke(
                raw.functions.updates.GetState(), retries=1, sleep_threshold=0, retry_delay=0
            )
        except errors.Unauthorized:
            await self._lose_auth(client)
        except Exception as exc:
            log.warning("telegram probe failed: %s", exc)

    async def stop(self) -> None:
        self._online = False
        await self._end_overload()
        await _force_close(self._client)

    async def abort(self) -> None:
        """Аварийная остановка, когда аренда потеряна (раздел 4.2 спеки, п. 6): сессия больше не
        принимает и не переподключается, обработчики диспетчера (и сторож обновлений) отменяются,
        хранилище закрывается без `save()`. `terminate()` не вызывается: он сохранил бы хранилище
        и доработал очередь диспетчера в конвейер, который после срока писать не может. Передача
        в конвейер обрывается первой: пока сессия останавливается (`Session.stop` ждёт задачи
        `handle_updates`), обработчики ещё разбирают очередь."""
        self._aborted = True
        self._online = False
        await self._end_overload(hard=True)
        client = self._client
        if client.session is not None:
            with suppress(Exception):
                await client.session.stop()
        tasks = [*client.dispatcher.handler_worker_tasks]
        if client.updates_watchdog_task is not None:
            tasks.append(client.updates_watchdog_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with suppress(Exception):
            await self._storage.close()

    @_fenced
    async def resolve(self, chat_id: int) -> None:
        from pyrogram import errors

        client = self._client
        if chat_id in self._peers:
            return
        try:
            peer = await client.resolve_peer(chat_id)
        except errors.FloodWait as exc:
            raise FloodWait(float(exc.seconds or 0)) from exc
        except errors.Unauthorized as exc:
            await self._lose_auth(client)
            raise TransportAuthLost(str(exc)) from exc
        if client is self._client:
            self._peers[chat_id] = peer

    async def _peer(self, client: Any, chat_id: int) -> Any:
        peer = self._peers.get(chat_id) if client is self._client else None
        return peer if peer is not None else await client.resolve_peer(chat_id)

    @_fenced
    async def send_text(self, chat_id: int, text: str, reply_to: int | None = None) -> int:
        from pyrogram import errors, raw

        client = self._client
        try:
            peer = await self._peer(client, chat_id)
            reply = raw.types.InputReplyToMessage(reply_to_msg_id=reply_to) if reply_to else None
            await client.invoke(
                raw.functions.messages.SendMessage(
                    peer=peer,
                    message=text,
                    random_id=client.rnd_id(),
                    reply_to=reply,
                    no_webpage=True,
                ),
                retries=1,
                sleep_threshold=0,
                retry_delay=0,
            )
        except errors.FloodWait as exc:
            raise FloodWait(float(exc.seconds or 0)) from exc
        except errors.Unauthorized as exc:
            await self._lose_auth(client)
            raise TransportAuthLost(str(exc)) from exc
        return 0

    @_fenced
    async def send_saved(self, text: str) -> None:
        from pyrogram import errors, raw

        client = self._client
        try:
            await client.invoke(
                raw.functions.messages.SendMessage(
                    peer=raw.types.InputPeerSelf(),
                    message=text,
                    random_id=client.rnd_id(),
                    no_webpage=True,
                ),
                retries=1,
                sleep_threshold=0,
                retry_delay=0,
            )
        except errors.FloodWait as exc:
            raise FloodWait(float(exc.seconds or 0)) from exc
        except errors.Unauthorized as exc:
            await self._lose_auth(client)
            raise TransportAuthLost(str(exc)) from exc

    @_fenced
    async def click(
        self, chat_id: int, message_id: int, data: str, timeout_s: float
    ) -> str | None:
        from pyrogram import errors, raw

        client = self._client
        try:
            peer = await self._peer(client, chat_id)
            answer = await client.invoke(
                raw.functions.messages.GetBotCallbackAnswer(
                    peer=peer, msg_id=message_id, data=data.encode()
                ),
                retries=1,
                timeout=timeout_s,
                sleep_threshold=0,
                retry_delay=0,
            )
        except TimeoutError:
            return None
        except errors.FloodWait as exc:
            raise FloodWait(float(exc.seconds or 0)) from exc
        except errors.Unauthorized as exc:
            await self._lose_auth(client)
            raise TransportAuthLost(str(exc)) from exc
        except errors.BotResponseTimeout:
            return None
        except errors.BadRequest as exc:
            raise TransportRejected(str(exc.ID or exc)) from exc
        message = getattr(answer, "message", None)
        return str(message) if message else None

    @_fenced
    async def forward(self, from_chat_id: int, message_id: int, to_chat_id: int) -> int:
        from pyrogram import errors, raw

        client = self._client
        random_id = client.rnd_id()
        try:
            to_peer = await client.resolve_peer(to_chat_id)
            from_peer = await client.resolve_peer(from_chat_id)
        except errors.FloodWait as exc:
            raise FloodWait(float(exc.seconds or 0)) from exc
        except errors.Unauthorized as exc:
            await self._lose_auth(client)
            raise TransportAuthLost(str(exc)) from exc
        except Exception as exc:
            # До пересылки дело не дошло: копии точно нет — отказ, а не неясный исход.
            raise TransportRejected(f"peer:{type(exc).__name__}") from exc
        try:
            # Одна попытка: повтор после тайм-аута мог бы переслать дважды.
            updates = await client.invoke(
                raw.functions.messages.ForwardMessages(
                    to_peer=to_peer, from_peer=from_peer, id=[message_id], random_id=[random_id]
                ),
                retries=1,
                sleep_threshold=0,
                retry_delay=0,
            )
        except errors.FloodWait as exc:
            raise FloodWait(float(exc.seconds or 0)) from exc
        except errors.Unauthorized as exc:
            await self._lose_auth(client)
            raise TransportAuthLost(str(exc)) from exc
        except (errors.BadRequest, errors.Forbidden) as exc:
            raise TransportRejected(str(exc.ID or exc)) from exc
        return _forwarded_id(updates, random_id)

    @_fenced
    async def check_group(self, chat_id: int) -> GroupInfo:
        from pyrogram import enums, errors

        client = self._client
        title: str | None = None
        try:
            chat = await client.get_chat(chat_id)
            title = getattr(chat, "title", None)
            if chat.type not in (
                enums.ChatType.GROUP,
                enums.ChatType.SUPERGROUP,
                enums.ChatType.FORUM,
            ):
                return GroupInfo("not_group", title)
            member = await client.get_chat_member(chat_id, "me")
        except errors.FloodWait as exc:
            raise FloodWait(float(exc.seconds or 0)) from exc
        except errors.Unauthorized as exc:
            await self._lose_auth(client)
            raise TransportAuthLost(str(exc)) from exc
        except errors.UserNotParticipant:
            return GroupInfo("not_member", title)
        except errors.RPCError:
            return GroupInfo("unavailable", title)
        if member.status in (enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED):
            return GroupInfo("not_member", title)
        return GroupInfo("ok", title)

    @_fenced
    async def join_chat(self, username: str, expect_id: int) -> JoinStatus:
        from pyrogram import errors, types

        client = self._client
        try:
            chat = await client.get_chat(username)
            if chat.id != expect_id:
                raise TransportRejected("chat_mismatch")
            result = await client.join_chat(username)
        except errors.FloodWait as exc:
            raise FloodWait(float(exc.seconds or 0)) from exc
        except errors.Unauthorized as exc:
            await self._lose_auth(client)
            raise TransportAuthLost(str(exc)) from exc
        except errors.UserAlreadyParticipant:
            return "already_member"
        except errors.InviteRequestSent:
            return "request_sent"
        except errors.RPCError as exc:
            raise TransportRejected(str(exc.ID or exc)) from exc
        if isinstance(result, types.ChatJoinResultDeclined):
            raise TransportRejected("join_declined")
        if isinstance(
            result, (types.ChatJoinResultRequestSent, types.ChatJoinResultGuardBotApprovalRequired)
        ):
            return "request_sent"
        return "joined"

    @_fenced
    async def fetch(self, chat_id: int, message_id: int) -> IncomingMessage | None:
        from pyrogram import errors

        client = self._client
        try:
            message = await client.get_messages(chat_id, message_id)
        except errors.FloodWait as exc:
            raise FloodWait(float(exc.seconds or 0)) from exc
        except errors.Unauthorized as exc:
            await self._lose_auth(client)
            raise TransportAuthLost(str(exc)) from exc
        if message is None or getattr(message, "empty", False):
            return None
        kind: MessageKind = "edit" if message.edit_date else "new"
        return to_incoming(message, kind=kind, received_at=datetime.now(UTC))

    @_fenced
    async def latest(self, reader: Reader) -> int | None:
        page = await self._history(reader, limit=1)
        return int(page[0].id) if page else None

    @_fenced
    async def read(self, reader: Reader, above: int, limit: int) -> list[Any]:
        """Самые новые сообщения чтения с `id > above`, не больше `limit` — страницами от новых
        к старым; неполная страница — последняя."""
        found: list[Any] = []
        offset_id = 0
        while len(found) < limit:
            size = min(HISTORY_PAGE, limit - len(found))
            page = await self._history(reader, limit=size, offset_id=offset_id, min_id=above)
            fresh = [m for m in page if m.id > above]
            found.extend(fresh)
            # Неполная страница (или дошли до отметки) — конец диапазона, без запроса за пустой
            # страницей: короткий обрыв стоит одного запроса (раздел 4.3 спеки).
            if len(fresh) < size:
                break
            offset_id = min(m.id for m in fresh)
        return [m for m in found if not getattr(m, "empty", False)]

    @_fenced
    async def tail(self, reader: Reader, upto: int, count: int) -> list[Any]:
        page = await self._history(reader, limit=count, offset_id=upto + 1)
        return [m for m in page if m.id <= upto and not getattr(m, "empty", False)]

    async def _history(
        self, reader: Reader, *, limit: int, offset_id: int = 0, min_id: int = 0
    ) -> list[Any]:
        """Страница истории чтения, от новых к старым: `messages.getHistory` всего чата или
        `messages.search` сообщений одного отправителя (`from_id`); `offset_id` и `min_id` —
        границы, сами не входящие."""
        from pyrogram import errors, raw, utils

        client = self._client
        chat_id, from_id = reader
        try:
            peer = await self._peer(client, chat_id)
            query: Any
            if from_id:
                query = raw.functions.messages.Search(
                    peer=peer,
                    q="",
                    filter=raw.types.InputMessagesFilterEmpty(),
                    min_date=0,
                    max_date=0,
                    offset_id=offset_id,
                    add_offset=0,
                    limit=limit,
                    max_id=0,
                    min_id=min_id,
                    hash=0,
                    from_id=await self._peer(client, from_id),
                )
            else:
                query = raw.functions.messages.GetHistory(
                    peer=peer,
                    offset_id=offset_id,
                    offset_date=0,
                    add_offset=0,
                    limit=limit,
                    max_id=0,
                    min_id=min_id,
                    hash=0,
                )
            answer = await client.invoke(query)
            return list(await utils.parse_messages(client, answer, replies=0))
        except errors.FloodWait as exc:
            raise FloodWait(float(exc.seconds or 0)) from exc
        except errors.Unauthorized as exc:
            await self._lose_auth(client)
            raise TransportAuthLost(str(exc)) from exc
        except (
            errors.ChannelInvalid,
            errors.ChannelPrivate,
            errors.PeerIdInvalid,
            errors.UserNotParticipant,
        ) as exc:
            raise ChatUnavailable(chat_id, type(exc).__name__) from exc


async def logout_offline(db: Database, box: SecretBox, config: AppConfig, account_id: int) -> None:
    """Выход из Telegram удаляемого аккаунта, движка которого на хосте нет (раздел 4.2 спеки):
    временный клиент на сессии из базы — только для `auth.LogOut`, затем сессия и пиры аккаунта
    удаляются из базы. Хост зовёт его через ограду аренды. Входа в сессии нет — клиент не
    поднимается; сбой выхода — исключение, но сессия из базы удалена."""
    from pyrogram import Client, errors, raw

    from app.db.tg_storage import PgSessionStorage

    storage = PgSessionStorage(db, account_id, box, set)
    await storage.open()
    if await storage.user_id() is None:
        await storage.delete()
        return
    client = Client(
        f"account-{account_id}",
        api_id=config.tg_api_id,
        api_hash=config.tg_api_hash.get_secret_value(),
        storage_engine=storage,
        no_updates=True,
        **_DEVICE,
    )
    failure: Exception | None = None
    try:
        if await client.connect():
            await client.invoke(
                raw.functions.auth.LogOut(), retries=1, sleep_threshold=0, retry_delay=0
            )
    except errors.Unauthorized:
        log.info("session already revoked, deleting it")
    except Exception as exc:
        failure = exc
    finally:
        await _force_close(client)
    await storage.delete()
    if failure is not None:
        raise failure
