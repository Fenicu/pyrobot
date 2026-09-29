from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

from app.engine.settings import ChatsSection
from app.engine.transport.kurigram import ChatFilter, KurigramTransport
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
    def __init__(self) -> None:
        self.deleted = False
        self.closed = False
        self.states: list[Any] = []

    async def close(self) -> None:
        self.closed = True

    async def delete(self) -> None:
        if self.deleted:
            raise FileNotFoundError("session file")
        self.deleted = True

    async def user_id(self) -> int | None:
        return EXPECTED

    async def get_update_states(self, _id: int) -> list[Any]:
        return self.states

    async def set_update_state(self, state: Any) -> None:
        self.states.append(state)

    async def save(self) -> None:
        return None


class FakeClient:
    """Минимальная модель pyrogram.Client: те же инварианты connect/initialize/stop."""

    def __init__(self, *, authorized: bool) -> None:
        self.storage = FakeStorage()
        self.authorized = authorized
        self.is_connected = False
        self.is_initialized = False
        self.errors: dict[str, BaseException] = {}
        self.invoked: list[tuple[str, dict[str, Any]]] = []
        self.get_me_calls = 0
        self.stop_error: BaseException | None = None
        self.me: Any = None
        self.stored: dict[tuple[int, int], Any] = {}
        self.responses: dict[str, Any] = {}
        self.chat: Any = None
        self.member: Any = None
        self.resolved: list[int] = []

    async def connect(self) -> bool:
        if self.is_connected:
            raise ConnectionError("Client is already connected")
        self.is_connected = True
        return self.authorized

    async def disconnect(self) -> None:
        if not self.is_connected:
            raise ConnectionError("Client is already disconnected")
        if self.is_initialized:
            raise ConnectionError("Can't disconnect an initialized client")
        await self.storage.close()
        self.is_connected = False

    async def initialize(self) -> None:
        if not self.is_connected:
            raise ConnectionError("Can't initialize a disconnected client")
        if self.is_initialized:
            raise ConnectionError("Client is already initialized")
        self.is_initialized = True

    async def stop(self) -> None:
        if self.stop_error is not None:
            raise self.stop_error
        if not self.is_initialized:
            raise ConnectionError("Client is already terminated")
        self.is_initialized = False
        await self.disconnect()

    async def invoke(self, query: Any, **kw: Any) -> Any:
        name = type(query).__name__
        self.invoked.append((name, kw))
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

    async def get_chat_member(self, chat_id: int, user_id: int | str) -> Any:
        err = self.errors.pop("GetChatMember", None)
        if err is not None:
            raise err
        return self.member

    async def get_messages(self, chat_id: int, message_ids: int) -> Any:
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
        err = self.errors.pop("SendCode", None)
        if err is not None:
            raise err
        return NS(phone_code_hash="hash")

    async def sign_in(self, phone: str, code_hash: str, code: str) -> Any:
        self.authorized = True
        return _user()


async def _drop(msg: IncomingMessage) -> None:
    return None


class FakeKurigram(KurigramTransport):
    def __init__(self, workdir: Path, *, authorized: bool = True) -> None:
        self.clients: list[FakeClient] = []
        self._first_authorized = authorized
        super().__init__(
            api_id=1,
            api_hash="x",
            workdir=workdir,
            chat_filter=ChatFilter.from_settings(ChatsSection()),
            sink=_drop,
        )

    def _make_client(self) -> FakeClient:
        client = FakeClient(authorized=self._first_authorized and not self.clients)
        self.clients.append(client)
        return client

    @property
    def client(self) -> FakeClient:
        return self.clients[-1]
