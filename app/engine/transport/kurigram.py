from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
from app.engine.transport.base import FloodWait, TransportAuthLost, TransportRejected
from app.engine.types import Button, IncomingMessage, MessageKind

log = logging.getLogger(__name__)
Sink = Callable[[IncomingMessage], Awaitable[None]]
JOIN_FIGHT = re.compile(r"join_fight_\w{11}\Z")
DIALOGS_WARMUP = 200


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
    return any(JOIN_FIGHT.match(b.switch or "") or JOIN_FIGHT.match(b.data or "") for b in inline)


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


@dataclass(frozen=True)
class ChatFilter:
    game_chat_id: int
    swinfo_chat_id: int
    swinfo_user_id: int
    smoothie_channel_id: int
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
        if chat == self.swinfo_chat_id:
            return bool(m.from_user and m.from_user.id == self.swinfo_user_id)
        if self.bulls_chat_id is not None and chat == self.bulls_chat_id:
            return has_join_fight(m)
        return False


async def _force_close(client: Any) -> None:
    # watchdog обновлений kurigram, умерший на отозванной сессии, перевыбрасывает
    # Unauthorized внутри terminate() ДО is_initialized=False — stop() пропускает
    # disconnect(), и утекают MTProto-сессия и sqlite-соединение storage. Форсируем
    # закрытие каждым шагом отдельно, чтобы ни один ресурс не остался открытым.
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


class KurigramTransport:
    def __init__(
        self, *, api_id: int, api_hash: str, workdir: Path, chat_filter: ChatFilter, sink: Sink
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._workdir = workdir
        self._filter = chat_filter
        self._sink = sink
        self.on_auth_lost: Callable[[], Awaitable[None]] | None = None
        self._me: Any = None
        self._client = self._make_client()

    def _make_client(self) -> Any:
        from pyrogram import Client
        from pyrogram.handlers import EditedMessageHandler, MessageHandler

        self._workdir.mkdir(parents=True, exist_ok=True)
        client = Client(
            "pyrobot",
            api_id=self._api_id,
            api_hash=self._api_hash,
            workdir=str(self._workdir),
            workers=1,
            skip_updates=False,
            sleep_threshold=10,
            device_model="pyrobot",
            app_version="2.0",
            system_version="Linux",
            lang_code="ru",
        )
        client.add_handler(MessageHandler(self._on_new))
        client.add_handler(EditedMessageHandler(self._on_edit))
        return client

    async def _on_new(self, _client: Any, message: Any) -> None:
        await self._forward(message, "new")

    async def _on_edit(self, _client: Any, message: Any) -> None:
        await self._forward(message, "edit")

    async def _forward(self, message: Any, kind: MessageKind) -> None:
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
        # повторно; новый клиент не открывает файл сессии до connect().
        if self._client is not client:
            return False
        self._me = None
        self._client = self._make_client()
        await _force_close(client)
        try:
            await client.storage.delete()
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("telegram session storage not deleted")
        return True

    async def _lose_auth(self, client: Any) -> None:
        if await self._reset_client(client):
            await self._auth_lost()

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

    async def check_password(self, password: str) -> int:
        from pyrogram import errors

        try:
            user = await self._client.check_password(password)
        except errors.PasswordHashInvalid as exc:
            raise InvalidPassword from exc
        return int(user.id)

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

    async def log_out(self) -> None:
        from pyrogram import errors, raw

        client = self._client
        failure: Exception | None = None
        if client.is_connected:
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
        await _force_close(self._client)

    async def send_text(self, chat_id: int, text: str, reply_to: int | None = None) -> int:
        from pyrogram import errors, raw

        client = self._client
        try:
            peer = await client.resolve_peer(chat_id)
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

    async def click(
        self, chat_id: int, message_id: int, data: str, timeout_s: float
    ) -> str | None:
        from pyrogram import errors, raw

        client = self._client
        try:
            peer = await client.resolve_peer(chat_id)
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
