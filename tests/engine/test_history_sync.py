import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest
from sqlalchemy import select

from app.db.base import Database
from app.db.chat_marks import ChatMarks
from app.db.journal import DbJournal
from app.db.models import MessageRow
from app.engine.bus import Bus
from app.engine.fence import LeaseLost
from app.engine.host.account import pipeline_deliver
from app.engine.notify import Level
from app.engine.parsing import default_parser
from app.engine.pipeline import NullReducer, Pipeline
from app.engine.settings import ChatsSection
from app.engine.transport.base import FloodWait, TransportAuthLost
from app.engine.transport.history import HistorySync, Reader, readers_for
from app.engine.transport.kurigram import ChatFilter, KurigramTransport, to_incoming
from app.engine.types import IncomingMessage
from tests.engine.helpers import GAME, until
from tests.engine.kurigram_fakes import FakeKurigram, FakeSession, long_fence

SMOOTHIE = -1001356300612
SWINFO = -1001109615116
SW_USER = 376592453
TEAM = -1001149209877
CHATS = ChatsSection()
INVITE = "join_fight_I16YW9RrvSq"
Key = tuple[int, int, str]


def _key(msg: IncomingMessage) -> Key:
    return msg.msg_id, msg.revision, msg.content_hash()


def _incoming(m: Any) -> IncomingMessage:
    return to_incoming(m, kind="edit" if m.edit_date else "new", received_at=datetime.now(UTC))


class FakeSource:
    """Telegram с историей чатов: сообщения kurigram, чтение от новых к старым, как у сервера.
    `gate` держит `read` уже прочитанным, `errors` — сбои следующих `read`, `broken` — чтения,
    любое обращение к которым сбоит."""

    def __init__(self) -> None:
        self.chats: dict[int, dict[int, NS]] = defaultdict(dict)
        self.calls: list[tuple[Any, ...]] = []
        self.errors: list[BaseException] = []
        self.broken: dict[Reader, BaseException] = {}
        self.gate: asyncio.Event | None = None

    def post(
        self,
        chat_id: int,
        msg_id: int,
        text: str | None = None,
        *,
        sender: int | None = None,
        age: timedelta = timedelta(seconds=5),
    ) -> NS:
        message = NS(
            id=msg_id,
            chat=NS(id=chat_id),
            from_user=NS(id=sender if sender is not None else abs(chat_id)),
            outgoing=False,
            date=datetime.now(UTC) - age,
            edit_date=None,
            text=text or f"m{msg_id}",
            caption=None,
            reply_markup=None,
        )
        self.chats[chat_id][msg_id] = message
        return message

    def edit(self, chat_id: int, msg_id: int, text: str) -> None:
        message = self.chats[chat_id][msg_id]
        message.text, message.edit_date = text, datetime.now(UTC)

    def get(self, chat_id: int, msg_id: int) -> NS:
        return self.chats[chat_id][msg_id]

    @property
    def reads(self) -> int:
        return sum(1 for call in self.calls if call[0] == "read")

    def _of(self, reader: Reader) -> list[NS]:
        chat_id, from_id = reader
        found = (
            m for m in self.chats[chat_id].values() if not from_id or m.from_user.id == from_id
        )
        return sorted(found, key=lambda m: m.id, reverse=True)

    async def latest(self, reader: Reader) -> int | None:
        self.calls.append(("latest", reader))
        if reader in self.broken:
            raise self.broken[reader]
        found = self._of(reader)
        return found[0].id if found else None

    async def read(self, reader: Reader, above: int, limit: int) -> list[NS]:
        self.calls.append(("read", reader, above, limit))
        if reader in self.broken:
            raise self.broken[reader]
        found = [m for m in self._of(reader) if m.id > above][:limit]
        if self.gate is not None:
            await self.gate.wait()
        if self.errors:
            raise self.errors.pop(0)
        return found

    async def tail(self, reader: Reader, upto: int, count: int) -> list[NS]:
        self.calls.append(("tail", reader, upto, count))
        return [m for m in self._of(reader) if m.id <= upto][:count]


class FakeJournal:
    """Журнал: `append` — живая запись конвейером, `deliver` — переданное проходом."""

    def __init__(self) -> None:
        self.rows: list[IncomingMessage] = []
        self.delivered: list[IncomingMessage] = []
        self._keys: set[tuple[int, Key]] = set()

    def append(self, msg: IncomingMessage) -> None:
        key = (msg.chat_id, _key(msg))
        if key not in self._keys:
            self._keys.add(key)
            self.rows.append(msg)

    async def known(self, chat_id: int, keys: list[Key]) -> set[Key]:
        return {key for key in keys if (chat_id, key) in self._keys}

    async def deliver(self, messages: list[IncomingMessage]) -> bool:
        self.delivered.extend(messages)
        for msg in messages:
            self.append(msg)
        return True


class MemoryMarks:
    def __init__(self, marks: dict[Reader, int] | None = None) -> None:
        self.marks = dict(marks or {})

    async def get(self, reader: Reader) -> int | None:
        return self.marks.get(reader)

    async def advance(self, reader: Reader, msg_id: int) -> None:
        self.marks[reader] = max(msg_id, self.marks.get(reader, msg_id))


class FakeTime:
    """Подменённый сон: спящие ждут, пока тест не передвинет время (`advance`)."""

    def __init__(self) -> None:
        self.now = 0.0
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []

    @property
    def waiting(self) -> list[float]:
        return sorted(when - self.now for when, _ in self._sleepers)

    async def sleep(self, seconds: float) -> None:
        entry = (self.now + seconds, asyncio.get_running_loop().create_future())
        self._sleepers.append(entry)
        try:
            await entry[1]
        finally:
            self._sleepers.remove(entry)

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for when, future in self._sleepers:
            if when <= self.now and not future.done():
                future.set_result(None)


class Recorder:
    def __init__(self) -> None:
        self.items: list[tuple[Level, str]] = []

    async def notify(self, level: Level, code: str, text: str) -> None:
        self.items.append((level, code))


class Rig:
    """Сверка на фейковом Telegram, журнале, отметках и сне."""

    def __init__(
        self,
        *,
        marks: dict[Reader, int] | None = None,
        readers: set[Reader] | None = None,
        source: Any = None,
        chats: ChatsSection = CHATS,
        online: Callable[[], bool] | None = None,
        **kw: Any,
    ) -> None:
        self.source = source if source is not None else FakeSource()
        self.journal = FakeJournal()
        self.marks = MemoryMarks(marks)
        self.time = FakeTime()
        self.notifier = Recorder()
        self.online = True
        self.sync = HistorySync(
            self.source,
            self.marks,  # type: ignore[arg-type]
            self.journal.known,
            self.journal.deliver,
            ChatFilter.from_settings(chats).accepts,
            self.notifier,
            readers if readers is not None else {(GAME, 0)},
            online or (lambda: self.online),
            sleep=self.time.sleep,
            **kw,
        )

    def live(self, chat_id: int, *msg_ids: int) -> None:
        """Сообщения, записанные в журнал живыми обновлениями."""
        for msg_id in msg_ids:
            self.journal.append(_incoming(self.source.get(chat_id, msg_id)))

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        task = asyncio.create_task(self.sync.run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def _ids(messages: list[IncomingMessage]) -> list[int]:
    return [m.msg_id for m in messages]


async def test_no_mark_sets_head_without_reading() -> None:
    rig = Rig(readers=readers_for(CHATS))
    for msg_id in range(1, 51):
        rig.source.post(GAME, msg_id)
    rig.source.post(SWINFO, 7, sender=SW_USER)
    rig.source.post(SWINFO, 9, sender=5)
    await rig.sync.pass_once()
    # Старая история не читается: бот, как и прежде, не загружает прошлое чата.
    assert rig.marks.marks == {(GAME, 0): 50, (SWINFO, SW_USER): 7}
    assert {call[0] for call in rig.source.calls} == {"latest"}
    assert rig.journal.delivered == []
    # Канал смузи пуст — отметку поставит проход, когда в нём появится сообщение. Следующий
    # проход прошлое тоже не дочитывает: на отметке и перед ней берутся только правки.
    rig.source.post(SMOOTHIE, 3)
    await rig.sync.pass_once()
    assert rig.marks.marks[(SMOOTHIE, 0)] == 3 and rig.journal.delivered == []
    assert ("tail", (GAME, 0), 50, 50) in rig.source.calls


async def test_only_unknown_or_changed_messages_delivered() -> None:
    rig = Rig(marks={(GAME, 0): 10})
    for msg_id in range(5, 14):
        rig.source.post(GAME, msg_id)
    # Журнал: до отметки — всё, кроме 6 (не читается заново: до отметки — только правки);
    # после неё вживую пришли 11 и 12, причём 12 — с другим содержимым (кнопки сменились без
    # даты правки): ревизия та же, хеш другой. 13 пропущено.
    rig.live(GAME, 5, 7, 8, 9, 10, 11)
    rig.journal.append(_incoming(NS(**{**vars(rig.source.get(GAME, 12)), "text": "m12 было"})))
    # Правки за время обрыва: 8 — среди 50 перед отметкой, 11 — после неё.
    rig.source.edit(GAME, 8, "m8 правка")
    rig.source.edit(GAME, 11, "m11 правка")
    await rig.sync.pass_once()
    delivered = [(m.msg_id, m.kind, m.text) for m in rig.journal.delivered]
    assert delivered == [
        (8, "edit", "m8 правка"),
        (11, "edit", "m11 правка"),
        (12, "new", "m12"),
        (13, "new", "m13"),
    ]
    assert rig.source.calls == [("read", (GAME, 0), 10, 1001), ("tail", (GAME, 0), 10, 50)]
    assert rig.marks.marks[(GAME, 0)] == 13
    # Следующий проход: всё уже в журнале — в конвейер ничего.
    rig.journal.delivered.clear()
    await rig.sync.pass_once()
    assert rig.journal.delivered == []
    assert rig.source.calls[-1] == ("tail", (GAME, 0), 13, 50)


async def test_more_than_limit_takes_last_and_warns() -> None:
    rig = Rig(marks={(GAME, 0): 100})
    for msg_id in range(101, 1601):
        rig.source.post(GAME, msg_id)
    await rig.sync.pass_once()
    assert ("read", (GAME, 0), 100, 1001) in rig.source.calls
    assert _ids(rig.journal.delivered) == list(range(601, 1601))
    assert rig.notifier.items == [("warn", "history_gap_truncated")]
    assert rig.marks.marks[(GAME, 0)] == 1600


async def test_recovered_flag_on_old_messages() -> None:
    rig = Rig(marks={(GAME, 0): 10})
    rig.source.post(GAME, 11, age=timedelta(minutes=10))
    rig.source.post(GAME, 12, age=timedelta(seconds=5))
    await rig.sync.pass_once()
    assert [(m.msg_id, m.recovered) for m in rig.journal.delivered] == [(11, True), (12, False)]


async def test_silent_gap_found_by_periodic_pass_within_five_minutes() -> None:
    rig = Rig(marks={(GAME, 0): 10})
    async with rig.running():
        rig.sync.request("online")
        await until(lambda: rig.source.reads == 1 and rig.time.waiting == [300.0])
        # Сообщение пропущено молча: Telegram не прислал ни обновления, ни разрыва.
        rig.source.post(GAME, 11)
        rig.time.advance(299.0)
        await asyncio.sleep(0.01)
        assert rig.journal.delivered == [] and rig.time.waiting == [1.0]
        rig.time.advance(1.0)
        await until(lambda: _ids(rig.journal.delivered) == [11])
        await until(lambda: rig.time.waiting == [300.0])
    assert rig.marks.marks[(GAME, 0)] == 11


async def test_request_during_pass_runs_again_after() -> None:
    rig = Rig(marks={(GAME, 0): 10})
    gate = rig.source.gate = asyncio.Event()
    async with rig.running():
        rig.sync.request("online")
        await until(lambda: rig.source.reads == 1)
        # Проход уже прочитал историю; пришло новое сообщение и несколько запросов.
        rig.source.post(GAME, 11)
        for reason in ("gap", "reconnect", "handle_updates"):
            rig.sync.request(reason)
        gate.set()
        # Запросы во время прохода — ещё один проход сразу после него, а не по одному на каждый.
        await until(lambda: rig.source.reads == 2 and rig.time.waiting == [300.0])
        await asyncio.sleep(0.01)
        assert rig.source.reads == 2
    assert _ids(rig.journal.delivered) == [11]
    assert rig.marks.marks[(GAME, 0)] == 11


async def _wait(rig: Rig, delay: float) -> None:
    await until(lambda: rig.time.waiting == [delay])


async def test_failed_pass_keeps_mark_and_retries_with_backoff() -> None:
    # Review Focus 5: read бросает FloodWait на старте → отметка прежняя, повтор через 30 с.
    # Что старт движка прохода не ждёт — tests/engine/host/test_account_runtime.py.
    rig = Rig(marks={(GAME, 0): 10})
    rig.source.post(GAME, 11)
    rig.source.errors = [FloodWait(40.0) for _ in range(5)]
    async with rig.running():
        rig.sync.request("online")
        for attempt, delay in enumerate((30.0, 60.0, 120.0, 300.0, 300.0), start=1):
            await _wait(rig, delay)
            assert rig.source.reads == attempt
            assert rig.marks.marks[(GAME, 0)] == 10 and rig.journal.delivered == []
            # Запрос повтор не торопит: сбой мог быть FloodWait.
            rig.sync.request("gap")
            await asyncio.sleep(0.01)
            assert rig.source.reads == attempt
            rig.time.advance(delay)
        await until(lambda: rig.marks.marks[(GAME, 0)] == 11)
        await _wait(rig, 300.0)
    assert _ids(rig.journal.delivered) == [11]


async def test_failing_reader_does_not_stop_others() -> None:
    # Чтение swinfo сломано насовсем (пира пользователя нет в хранилище), а чтения идут по порядку
    # id чата: смузи, swinfo, игра — чат игры всё равно сверяется.
    rig = Rig(
        readers=readers_for(CHATS),
        marks={(SMOOTHIE, 0): 3, (SWINFO, SW_USER): 5, (GAME, 0): 10},
    )
    rig.source.post(SMOOTHIE, 4)
    rig.source.post(GAME, 11)
    rig.source.broken[(SWINFO, SW_USER)] = ValueError("PEER_ID_INVALID")
    with pytest.raises(ValueError, match="PEER_ID_INVALID"):
        await rig.sync.pass_once()
    assert rig.marks.marks == {(SMOOTHIE, 0): 4, (SWINFO, SW_USER): 5, (GAME, 0): 11}
    assert _ids(rig.journal.delivered) == [4, 11]
    # Проход всё же неудачен: повтор по backoff, удачные чтения его отметки сохраняют.
    rig.source.post(GAME, 12)
    async with rig.running():
        rig.sync.request("online")
        await _wait(rig, 30.0)
    assert rig.marks.marks[(GAME, 0)] == 12 and rig.marks.marks[(SWINFO, SW_USER)] == 5


@pytest.mark.parametrize(
    "failure",
    [FloodWait(30.0), LeaseLost("lease"), TransportAuthLost("revoked")],
    ids=["flood", "lease", "auth"],
)
async def test_account_failure_stops_pass(failure: Exception) -> None:
    # Сбой всего аккаунта: следующие чтения упёрлись бы в него же — проход обрывается сразу.
    rig = Rig(readers=readers_for(CHATS), marks={(SMOOTHIE, 0): 3, (GAME, 0): 10})
    rig.source.post(GAME, 11)
    rig.source.broken[(SMOOTHIE, 0)] = failure
    with pytest.raises(type(failure)):
        await rig.sync.pass_once()
    assert [call[1] for call in rig.source.calls] == [(SMOOTHIE, 0)]
    assert rig.marks.marks == {(SMOOTHIE, 0): 3, (GAME, 0): 10}


async def test_no_pass_before_online_or_for_non_main_session() -> None:
    # Транспорт просит проход только после выхода в онлайн и при переподключении главной сессии.
    requests: list[str] = []
    t = FakeKurigram()
    t.on_history_needed = requests.append
    client = t.client
    await t._on_connect(client, client.session)
    assert await t.connect()
    # Первый connect(): вход, проверка привязки и go_online ещё впереди.
    await t._on_connect(client, client.session)
    assert requests == [] and not t.online
    await t.go_online()
    assert requests == ["online"] and t.online
    # Сессии других DC и медиа не в счёт.
    await t._on_connect(client, FakeSession(t.events))
    assert requests == ["online"]
    await t._on_connect(client, client.session)
    assert requests == ["online", "reconnect"]
    await t.log_out()
    assert not t.online
    await t._on_connect(t.client, t.client.session)
    assert requests == ["online", "reconnect"]

    # Пока аккаунт не онлайн, проходов нет — ни по запросу, ни по периоду.
    rig = Rig(marks={(GAME, 0): 10})
    rig.online = False
    async with rig.running():
        rig.sync.request("gap")
        await asyncio.sleep(0.01)
        await _wait(rig, 300.0)
        rig.time.advance(300.0)
        await asyncio.sleep(0.01)
        await _wait(rig, 300.0)
        assert rig.source.calls == []
        rig.online = True
        rig.sync.request("online")
        await until(lambda: rig.source.reads == 1)


async def test_reconnect_gap_recovered_even_if_live_messages_came_first() -> None:
    t = FakeKurigram()
    rig = Rig(marks={(GAME, 0): 10}, online=lambda: t.online)
    t.on_history_needed = rig.sync.request
    for msg_id in range(1, 11):
        rig.source.post(GAME, msg_id)
    rig.live(GAME, *range(1, 11))
    async with rig.running():
        assert await t.connect()
        await t.go_online()
        await until(lambda: rig.source.reads == 1 and rig.time.waiting == [300.0])
        # Обрыв: 11 и 12 пропущены; после переподключения живые 13 и 14 записаны раньше прохода.
        for msg_id in (11, 12, 13, 14):
            rig.source.post(GAME, msg_id)
        rig.live(GAME, 13, 14)
        gate = rig.source.gate = asyncio.Event()
        await t._on_connect(t.client, t.client.session)
        # Обработчик подключения прохода не ждёт: kurigram выполняет его внутри Session.start.
        await until(lambda: rig.source.reads == 2)
        assert not gate.is_set()
        gate.set()
        await until(lambda: rig.marks.marks[(GAME, 0)] == 14)
    assert _ids(rig.journal.delivered) == [11, 12]
    assert sorted(_ids(rig.journal.rows)) == list(range(1, 15))


@pytest.mark.db
async def test_crash_after_pts_saved_message_journaled_once(clean_db: Database) -> None:
    source = FakeSource()
    marks = ChatMarks(clean_db, 1)

    @contextlib.asynccontextmanager
    async def engine() -> AsyncIterator[tuple[Pipeline, HistorySync]]:
        """Процесс: конвейер на журнале в базе и сверка."""
        journal = DbJournal(clean_db, 1)
        pipeline = Pipeline(
            journal=journal, parser=default_parser(CHATS), reducer=NullReducer(), bus=Bus()
        )
        sync = HistorySync(
            source,
            marks,
            journal.known,
            pipeline_deliver(pipeline),
            ChatFilter.from_settings(CHATS).accepts,
            Recorder(),
            {(GAME, 0)},
            lambda: True,
        )
        task = asyncio.create_task(pipeline.run())
        try:
            yield pipeline, sync
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    source.post(GAME, 1, "🔋205%")
    async with engine() as (pipeline, sync):
        await sync.pass_once()
        await pipeline.submit(_incoming(source.get(GAME, 1)))
        assert await pipeline.drain(5.0)
        # kurigram сохранил pts сообщения 2 до того, как оно дошло до журнала, — и процесс упал.
        source.post(GAME, 2, "😴 Ты уснул")
    # После рестарта догонка kurigram его бы не вернула (pts сохранён), сверка — возвращает.
    async with engine() as (pipeline, sync):
        await sync.pass_once()
        assert await marks.get((GAME, 0)) == 2
        # Оно же вживую (сервер прислал повторно) и ещё один проход — в журнале одна строка.
        await pipeline.submit(_incoming(source.get(GAME, 2)))
        assert await pipeline.drain(5.0)
        await sync.pass_once()
    async with clean_db.sessions() as session:
        rows = list(await session.scalars(select(MessageRow.msg_id).order_by(MessageRow.id)))
    assert rows == [1, 2]


@pytest.mark.db
async def test_sender_change_starts_new_reader_from_head(clean_db: Database) -> None:
    marks = ChatMarks(clean_db, 1)
    old, new = SW_USER, 555
    await marks.advance((SWINFO, old), 100)
    rig = Rig(readers=set())
    for msg_id, sender in ((90, old), (100, old), (120, new), (140, new), (150, old)):
        rig.source.post(SWINFO, msg_id, sender=sender)
    # Настройки: в том же чате swinfo — другой отправитель; при старте движка отметка прежнего
    # удаляется, к новому она не применяется.
    chats = ChatsSection(swinfo_user_id=new)
    readers = readers_for(chats)
    assert await marks.prune(readers) == 1
    sync = HistorySync(
        rig.source,
        marks,
        rig.journal.known,
        rig.journal.deliver,
        ChatFilter.from_settings(chats).accepts,
        rig.notifier,
        {(SWINFO, new)},
        lambda: True,
    )
    await sync.pass_once()
    assert await marks.get((SWINFO, new)) == 140
    assert await marks.get((SWINFO, old)) is None
    assert rig.source.calls == [("latest", (SWINFO, new))]
    assert rig.journal.delivered == []


class Telegram:
    """Сервер для настоящего клиента kurigram: история чатов в сыром виде, ответы на
    `messages.getHistory` и `messages.search` — от новых к старым, не больше `limit`."""

    def __init__(self) -> None:
        self.messages: dict[int, list[Any]] = defaultdict(list)
        self.queries: list[Any] = []
        self.fail: Exception | None = None

    def post(self, chat_id: int, msg_id: int, *, sender: int, invite: bool = False) -> None:
        from pyrogram import raw

        markup = None
        if invite:
            button = raw.types.KeyboardInlineButton(
                text="Бой", type=raw.types.InlineButtonTypeSwitchInline(query=INVITE)
            )
            markup = raw.types.ReplyInlineMarkup(
                rows=[raw.types.KeyboardInlineButtonRow(buttons=[button])]
            )
        self.messages[chat_id].append(
            raw.types.Message(
                id=msg_id,
                peer_id=_peer(chat_id),
                from_id=raw.types.PeerUser(user_id=sender),
                date=int(datetime.now(UTC).timestamp()),
                message=f"m{msg_id}",
                reply_markup=markup,
                restriction_reason=[],
                entities=[],
            )
        )

    async def resolve_peer(self, peer_id: int) -> Any:
        from pyrogram import raw

        if peer_id < 0:
            return raw.types.InputPeerChannel(channel_id=_channel(peer_id), access_hash=0)
        return raw.types.InputPeerUser(user_id=peer_id, access_hash=0)

    async def invoke(self, query: Any, **_: Any) -> Any:
        from pyrogram import raw, utils

        self.queries.append(query)
        if isinstance(query, raw.functions.updates.GetDifference) and self.fail is not None:
            raise self.fail
        assert isinstance(query, raw.functions.messages.GetHistory | raw.functions.messages.Search)
        peer = query.peer
        chat_id = (
            utils.get_channel_id(peer.channel_id)
            if isinstance(peer, raw.types.InputPeerChannel)
            else peer.user_id
        )
        sender = getattr(getattr(query, "from_id", None), "user_id", None)
        found = [
            m
            for m in sorted(self.messages[chat_id], key=lambda m: m.id, reverse=True)
            if (not query.offset_id or m.id < query.offset_id)
            and m.id > query.min_id
            and (sender is None or m.from_id.user_id == sender)
        ][: query.limit]
        users = {m.from_id.user_id for m in found} | ({chat_id} if chat_id > 0 else set())
        return raw.types.messages.Messages(
            messages=found,
            chats=[_chat(chat_id)] if chat_id < 0 else [],
            users=[raw.types.User(id=u, access_hash=0, first_name=f"u{u}") for u in users],
            topics=[],
        )


def _channel(chat_id: int) -> int:
    return -chat_id - 1_000_000_000_000


def _peer(chat_id: int) -> Any:
    from pyrogram import raw

    if chat_id < 0:
        return raw.types.PeerChannel(channel_id=_channel(chat_id))
    return raw.types.PeerUser(user_id=chat_id)


def _chat(chat_id: int) -> Any:
    from pyrogram import raw

    return raw.types.Channel(
        id=_channel(chat_id),
        title="чат",
        photo=raw.types.ChatPhotoEmpty(),
        date=0,
        megagroup=True,
        access_hash=0,
        usernames=[],
        restriction_reason=[],
    )


@contextlib.asynccontextmanager
async def kurigram(tg: Telegram) -> AsyncIterator[KurigramTransport]:
    """Транспорт с настоящим клиентом kurigram (без сети) на сервере `tg`."""
    from pyrogram.storage import SQLiteStorage

    storage = SQLiteStorage("test", Path("."), in_memory=True)
    await storage.open()
    try:
        transport = KurigramTransport(
            api_id=1,
            api_hash="x",
            account_id=1,
            storage=storage,  # type: ignore[arg-type]
            fence=long_fence(),
            chat_filter=ChatFilter.from_settings(CHATS),
            sink=FakeJournal().deliver,  # type: ignore[arg-type]
        )
        transport._client.invoke = tg.invoke
        transport._client.resolve_peer = tg.resolve_peer
        yield transport
    finally:
        await storage.close()


async def test_filtered_chat_mark_is_chat_head_short_gap_one_request() -> None:
    tg = Telegram()
    # Приглашения к биржевикам — в чате команды: журнал берёт из него только их.
    chats = ChatsSection(bulls_invite_chat_id=TEAM)
    for msg_id in range(1, 300):
        tg.post(TEAM, msg_id, sender=5)
    async with kurigram(tg) as t:
        rig = Rig(source=t, chats=chats, readers={(TEAM, 0)})
        await rig.sync.pass_once()
        assert rig.marks.marks == {(TEAM, 0): 299}
        tg.post(TEAM, 300, sender=5)
        tg.post(TEAM, 301, sender=6, invite=True)
        tg.post(TEAM, 302, sender=5)
        tg.queries.clear()
        await rig.sync.pass_once()
    # Отметка — самое новое сообщение чата, а не последнее приглашение: короткий обрыв — один
    # запрос новых сообщений (и один — 50 на отметке и перед ней).
    assert [(type(q).__name__, q.offset_id, q.min_id, q.limit) for q in tg.queries] == [
        ("GetHistory", 0, 299, 100),
        ("GetHistory", 300, 0, 50),
    ]
    assert rig.marks.marks == {(TEAM, 0): 302}
    assert _ids(rig.journal.delivered) == [301] and rig.notifier.items == []


async def test_swinfo_read_by_sender_and_shared_invites_chat_has_two_readers() -> None:
    chats = ChatsSection(bulls_invite_chat_id=SWINFO)
    readers = readers_for(chats)
    assert readers == {(GAME, 0), (SMOOTHIE, 0), (SWINFO, SW_USER), (SWINFO, 0)}
    tg = Telegram()
    tg.post(SWINFO, 2, sender=SW_USER)
    for msg_id in range(3, 20):
        tg.post(SWINFO, msg_id, sender=5)
    tg.post(SWINFO, 20, sender=SW_USER)
    for msg_id in range(21, 31):
        tg.post(SWINFO, msg_id, sender=5)
    async with kurigram(tg) as t:
        rig = Rig(
            source=t,
            chats=chats,
            readers=readers,
            marks={(SWINFO, SW_USER): 1, (SWINFO, 0): 1},
            limit=5,
        )
        await rig.sync.pass_once()
    searches = [q for q in tg.queries if type(q).__name__ == "Search"]
    assert searches and all(q.from_id.user_id == SW_USER for q in searches)
    histories = {type(q).__name__ for q in tg.queries} - {"Search"}
    assert histories == {"GetHistory"}
    # Чужие сообщения общего чата вытеснили swinfo из лимита чтения всего чата, но не из
    # чтения по отправителю.
    assert _ids(rig.journal.delivered) == [2, 20]
    assert rig.notifier.items == [("warn", "history_gap_truncated")]
    assert rig.marks.marks == {(SWINFO, SW_USER): 20, (SWINFO, 0): 30}


async def test_gap_signals_and_handle_updates_error_request_pass() -> None:
    from pyrogram import raw

    tg = Telegram()
    async with kurigram(tg) as t:
        requests: list[str] = []
        t.on_history_needed = requests.append
        client = t._client
        # Переподключение главной сессии — обработчик подключения транспорта.
        assert client.connect_handler == t._on_connect
        await client.handle_updates(raw.types.UpdatesTooLong())
        channel = raw.types.UpdateChannelTooLong(channel_id=_channel(SWINFO))
        await client.handle_updates(
            raw.types.Updates(updates=[channel], users=[], chats=[], date=0, seq=0)
        )
        await client.handle_updates(raw.types.UpdateShort(update=channel, date=0))
        assert requests == ["gap", "gap", "gap"]
        # Обычные обновления прохода не просят.
        await client.handle_updates(
            raw.types.Updates(updates=[], users=[], chats=[], date=0, seq=0)
        )
        assert requests == ["gap", "gap", "gap"]
        # Личное сообщение игрового бота (UpdateShortMessage): GetDifference не удался — kurigram
        # только пишет в лог, и сообщение пропало бы без следа.
        tg.fail = OSError("connection lost")
        with pytest.raises(OSError, match="connection lost"):
            await client.handle_updates(
                raw.types.UpdateShortMessage(
                    id=7, user_id=GAME, message="🔋", pts=10, pts_count=1, date=0
                )
            )
        assert requests == ["gap", "gap", "gap", "handle_updates"]
