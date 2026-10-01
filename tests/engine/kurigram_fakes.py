import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from types import SimpleNamespace as NS
from typing import Any

from app.engine.fence import Fence
from app.engine.settings import ChatsSection
from app.engine.transport.kurigram import ChatFilter, KurigramTransport, Sink
from app.engine.types import IncomingMessage

EXPECTED = 267519921


def _user() -> Any:
    # pyrogram импортируется лениво, внутри работающего цикла, как и в приложении:
    # импорт на этапе сбора тестов создаёт свой event loop с DeprecationWarning.
    from pyrogram import types

    return types.User(id=EXPECTED)


def rpc_error(name: str) -> Exception:
    from pyrogram import errors

    return getattr(errors, name)()  # type: ignore[no-any-return]


class FakeStorage:
    """Хранилище с интерфейсом `PgSessionStorage`, которым пользуется транспорт: одно на все
    клиенты транспорта, `delete()` сбрасывает вошедшего пользователя."""

    def __init__(self, events: list[str], *, user_id: int | None) -> None:
        self.events = events
        self.deleted = False
        self.closed = False
        self.states: list[Any] = []
        self._user_id = user_id

    async def open(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        self.events.append("storage.close")

    async def delete(self) -> None:
        self.deleted = True
        self._user_id = None

    async def user_id(self, value: Any = object) -> int | None:
        if value is object:
            return self._user_id
        self._user_id = value
        return None

    async def get_update_states(self, _id: int) -> list[Any]:
        return self.states

    async def set_update_state(self, state: Any) -> None:
        self.states.append(state)

    async def save(self) -> None:
        self.events.append("storage.save")


class FakeSession:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.stopped = False
        # Остановка ждёт его, если задан: задачи `handle_updates` ещё идут.
        self.hold: asyncio.Event | None = None

    async def stop(self) -> None:
        # Как `Session.stop` kurigram: ждёт приёма, закрытия соединения и задач `handle_updates`
        # — в это время работают и обработчики диспетчера; останавливающаяся или остановленная
        # сессия — ничего.
        if self.stopped:
            return
        self.stopped = True
        for _ in range(3):
            await asyncio.sleep(0)
        if self.hold is not None:
            await self.hold.wait()
        self.events.append("session.stop")


class FakeDispatcher:
    """Очередь обновлений и обработчики kurigram: `stop()` дорабатывает очередь, как
    `Dispatcher.stop`. Обработчик (`handler`) задаёт тест, которому нужна доставка."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.updates_queue: asyncio.Queue[Any] = asyncio.Queue()
        self.handler_worker_tasks: list[asyncio.Task[None]] = []
        self.handler: Callable[[Any], Awaitable[None]] | None = None

    def start(self) -> None:
        if self.handler is not None:
            self.handler_worker_tasks.append(asyncio.create_task(self._worker(self.handler)))

    async def _worker(self, handler: Callable[[Any], Awaitable[None]]) -> None:
        try:
            while (update := await self.updates_queue.get()) is not None:
                await handler(update)
        except asyncio.CancelledError:
            self.events.append("handler.cancelled")
            raise

    async def stop(self) -> None:
        for _ in self.handler_worker_tasks:
            self.updates_queue.put_nowait(None)
        await asyncio.gather(*self.handler_worker_tasks)
        self.handler_worker_tasks.clear()


class FakeClient:
    """Минимальная модель pyrogram.Client: те же инварианты connect/initialize/stop; вошёл ли
    клиент — по `user_id` хранилища, как у kurigram."""

    def __init__(self, storage: Any, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.storage = storage
        self.session: FakeSession | None = None
        self.dispatcher = FakeDispatcher(self.events)
        self.updates_watchdog_task: asyncio.Task[None] | None = None
        self.is_connected = False
        self.is_initialized = False
        self.errors: dict[str, BaseException] = {}
        # Вызовы с этими именами висят, пока их не отменят (`cancelled`).
        self.hang: set[str] = set()
        self.cancelled: list[str] = []
        self.invoked: list[tuple[str, dict[str, Any]]] = []
        self.get_me_calls = 0
        self.stop_error: BaseException | None = None
        self.me: Any = None
        self.stored: dict[tuple[int, int], Any] = {}
        self.responses: dict[str, Any] = {}
        self.chat: Any = None
        self.member: Any = None
        self.resolved: list[int] = []
        # Вступления в чаты (`join_chat`) и что отвечает очередное из них.
        self.joined: list[int | str] = []
        self.join_result: Any = None

    async def connect(self) -> bool:
        if self.is_connected:
            raise ConnectionError("Client is already connected")
        await self.storage.open()
        self.session = FakeSession(self.events)
        self.is_connected = True
        return bool(await self.storage.user_id())

    async def disconnect(self) -> None:
        if not self.is_connected:
            raise ConnectionError("Client is already disconnected")
        if self.is_initialized:
            raise ConnectionError("Can't disconnect an initialized client")
        self.events.append("disconnect")
        assert self.session is not None
        await self.session.stop()
        await self.storage.close()
        self.session = None
        self.is_connected = False

    async def initialize(self) -> None:
        if not self.is_connected:
            raise ConnectionError("Can't initialize a disconnected client")
        if self.is_initialized:
            raise ConnectionError("Client is already initialized")
        self.dispatcher.start()
        self.is_initialized = True

    async def terminate(self) -> None:
        if not self.is_initialized:
            raise ConnectionError("Client is already terminated")
        self.events.append("terminate")
        await self.storage.save()
        await self.dispatcher.stop()
        self.is_initialized = False

    async def stop(self) -> None:
        if self.stop_error is not None:
            raise self.stop_error
        await self.terminate()
        await self.disconnect()

    async def _hang(self, name: str) -> None:
        if name not in self.hang:
            return
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.append(name)
            raise

    async def invoke(self, query: Any, **kw: Any) -> Any:
        name = type(query).__name__
        self.invoked.append((name, kw))
        await self._hang(name)
        err = self.errors.pop(name, None)
        if err is not None:
            raise err
        if name == "GetState":
            return NS(pts=1, qts=0, date=0, seq=0)
        if name in self.responses:
            return self.responses[name]
        return NS(message=None)

    async def resolve_peer(self, chat_id: int) -> Any:
        err = self.errors.pop("ResolvePeer", None)
        if err is not None:
            raise err
        self.resolved.append(chat_id)
        return NS(id=chat_id)

    def rnd_id(self) -> int:
        return 1

    async def get_chat(self, chat_id: int) -> Any:
        err = self.errors.pop("GetChat", None)
        if err is not None:
            raise err
        return self.chat

    async def join_chat(self, chat_id: int | str) -> Any:
        err = self.errors.pop("JoinChat", None)
        if err is not None:
            raise err
        self.joined.append(chat_id)
        return self.join_result

    async def get_chat_member(self, chat_id: int, user_id: int | str) -> Any:
        err = self.errors.pop("GetChatMember", None)
        if err is not None:
            raise err
        return self.member

    async def get_messages(self, chat_id: int, message_ids: int) -> Any:
        self.invoked.append(("GetMessages", {}))
        err = self.errors.pop("GetMessages", None)
        if err is not None:
            raise err
        return self.stored.get((chat_id, message_ids))

    async def get_me(self) -> Any:
        self.get_me_calls += 1
        err = self.errors.pop("GetMe", None)
        if err is not None:
            raise err
        return _user()

    async def get_dialogs(self, limit: int = 0) -> AsyncIterator[Any]:
        for item in ():
            yield item

    async def send_phone_number_code(self, phone: str) -> Any:
        self.invoked.append(("SendCode", {}))
        err = self.errors.pop("SendCode", None)
        if err is not None:
            raise err
        return NS(phone_code_hash="hash")

    async def sign_in(self, phone: str, code_hash: str, code: str) -> Any:
        await self.storage.user_id(EXPECTED)
        return _user()


async def _drop(msg: IncomingMessage) -> None:
    return None


def long_fence() -> Fence:
    """Ограда, срок которой за время теста не наступит."""
    return Fence(1, 1, time.monotonic() + 3600.0)


class FakeKurigram(KurigramTransport):
    """Транспорт на фейковых клиентах. `dispatch` — у каждого клиента обработчик диспетчера
    передаёт обновления в транспорт, как `MessageHandler` kurigram; `backlog` — очередь
    конвейера; `client_errors` — сбои вызовов у следующего созданного клиента."""

    def __init__(
        self,
        *,
        authorized: bool = True,
        fence: Fence | None = None,
        sink: Sink = _drop,
        backlog: Callable[[], int] = lambda: 0,
        dispatch: bool = False,
    ) -> None:
        self.clients: list[FakeClient] = []
        # События клиентов и хранилища по порядку: сессия, обработчики, хранилище.
        self.events: list[str] = []
        self.storage = FakeStorage(self.events, user_id=EXPECTED if authorized else None)
        self._dispatch = dispatch
        self.client_errors: dict[str, BaseException] = {}
        super().__init__(
            api_id=1,
            api_hash="x",
            account_id=1,
            storage=self.storage,  # type: ignore[arg-type]
            fence=fence or long_fence(),
            chat_filter=ChatFilter.from_settings(ChatsSection()),
            sink=sink,
            backlog=backlog,
        )

    def _make_client(self) -> FakeClient:
        client = FakeClient(self.storage, self.events)
        client.errors.update(self.client_errors)
        self.client_errors.clear()
        if self._dispatch:
            client.dispatcher.handler = lambda update: self._on_new(client, update)
        self.clients.append(client)
        return client

    @property
    def client(self) -> FakeClient:
        return self.clients[-1]
