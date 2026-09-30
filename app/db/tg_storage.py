"""Сессия kurigram в Postgres вместо файла `*.session`: поля сессии и пиры настроенных чатов лежат
в базе, остальное (другие пиры, usernames, состояние обновлений) — в памяти процесса.

Импорт pyrogram создаёт event loop с DeprecationWarning (в тестах — ошибка), поэтому модуль
подключают лениво, внутри работающего цикла, как и сам pyrogram в `transport/kurigram.py`."""

import asyncio
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pyrogram import raw
from pyrogram.storage import SQLiteStorage, Storage, UpdateState
from pyrogram.storage.sqlite_storage import get_input_peer
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import Database
from app.db.crypto import SecretBox
from app.db.models import TgPeer, TgSession

# Назначение `auth_key` в AAD блоба: `pyrobot:auth_key:<account_id>`.
AUTH_KEY_PURPOSE = "auth_key"

# Поля новой сессии — как у `SQLiteStorage.create()`: DC 2, боевой сервер, вход ещё не выполнен.
_NEW_SESSION: dict[str, Any] = {
    "dc_id": 2,
    "api_id": None,
    "test_mode": None,
    "auth_key": None,
    "date": 0,
    "user_id": None,
    "is_bot": None,
    "server_address": "149.154.167.51",
    "port": 443,
}


@dataclass(frozen=True)
class _Peer:
    access_hash: int
    type: str
    phone_number: str | None
    # Когда пир обновлялся: по нему usernames устаревают, как у `SQLiteStorage`.
    seen_at: float

    @property
    def row(self) -> tuple[int, str, str | None]:
        return self.access_hash, self.type, self.phone_number


def _merge(old: UpdateState, new: UpdateState) -> UpdateState:
    """Пустые поля нового состояния не затирают старые — как `COALESCE` у `SQLiteStorage`."""
    merged = (
        n if n is not None else o
        for n, o in zip(
            (new.pts, new.qts, new.date, new.seq),
            (old.pts, old.qts, old.date, old.seq),
            strict=True,
        )
    )
    return UpdateState(new.id, *merged)


class PgSessionStorage(Storage):
    """Хранилище kurigram одного аккаунта. Строка `tg_sessions` пишется в `save()` и при смене
    любого поля сессии (они меняются только при входе и переезде на другой DC): сбой между
    `user_id` и `is_bot` не оставит сессию, которая читается как пустая. `auth_key` лежит
    зашифрованным, привязанным к аккаунту. Пиры
    с `id` из `peer_ids()` дополнительно пишутся в `tg_peers` — kurigram находит их и до прогрева
    кэша диалогов. Состояние обновлений и usernames в базу не попадают."""

    USERNAME_TTL = SQLiteStorage.USERNAME_TTL

    def __init__(
        self,
        db: Database,
        account_id: int,
        box: SecretBox,
        peer_ids: Callable[[], set[int]],
    ) -> None:
        self._db = db
        self._account_id = account_id
        self._box = box
        self._peer_ids = peer_ids
        # Записи строки сессии идут по очереди: снимок полей берётся под замком, поэтому более
        # поздняя запись не затирается более ранней.
        self._write_lock = asyncio.Lock()
        self._reset()

    def _reset(self) -> None:
        self._fields = dict(_NEW_SESSION)
        self._peers: dict[int, _Peer] = {}
        # Что из пиров уже лежит в `tg_peers`: одинаковое повторение в базу не пишется.
        self._persisted: dict[int, tuple[int, str, str | None]] = {}
        self._usernames: dict[int, tuple[str, ...]] = {}
        self._states: dict[int, UpdateState] = {}

    async def open(self) -> None:
        """Загружает строку сессии и пиры аккаунта; блоб, который не расшифровался, —
        `Undecryptable`. Повторный вызов читает всё заново: память после `close()` пуста."""
        self._reset()
        async with self._db.sessions() as session:
            row = await session.get(TgSession, self._account_id)
            peers = (
                await session.scalars(select(TgPeer).where(TgPeer.account_id == self._account_id))
            ).all()
        if row is not None:
            auth_key = (
                None
                if row.auth_key is None
                else self._box.open(row.auth_key, AUTH_KEY_PURPOSE, self._account_id)
            )
            self._fields = {
                "dc_id": row.dc_id,
                "api_id": row.api_id,
                "test_mode": row.test_mode,
                "auth_key": auth_key,
                "date": row.date,
                "user_id": row.user_id,
                "is_bot": row.is_bot,
                "server_address": row.server_address,
                "port": row.port,
            }
        for peer in peers:
            loaded = _Peer(peer.access_hash or 0, peer.type, peer.phone_number, time.time())
            self._peers[peer.id] = loaded
            self._persisted[peer.id] = loaded.row

    async def save(self) -> None:
        self._fields["date"] = int(time.time())
        await self._write_session()

    async def close(self) -> None:
        # Своего соединения у хранилища нет: база общая, её закрывает владелец.
        pass

    async def delete(self) -> None:
        """Удаляет сессию и пиры аккаунта из базы и сбрасывает память к новой сессии."""
        async with self._db.sessions() as session, session.begin():
            await session.execute(delete(TgPeer).where(TgPeer.account_id == self._account_id))
            await session.execute(
                delete(TgSession).where(TgSession.account_id == self._account_id)
            )
        self._reset()

    async def _write_session(self) -> None:
        async with self._write_lock:
            row = dict(self._fields)
            if row["auth_key"] is not None:
                row["auth_key"] = self._box.seal(
                    row["auth_key"], AUTH_KEY_PURPOSE, self._account_id
                )
            stmt = pg_insert(TgSession).values(account_id=self._account_id, **row)
            stmt = stmt.on_conflict_do_update(
                index_elements=[TgSession.account_id],
                set_={**{name: stmt.excluded[name] for name in row}, "updated_at": func.now()},
            )
            async with self._db.sessions() as session, session.begin():
                await session.execute(stmt)

    async def update_peers(self, peers: Iterable[tuple[int, int, str, str | None]]) -> None:
        now = time.time()
        persist = self._peer_ids()
        changed: dict[int, tuple[int, str, str | None]] = {}
        for peer_id, access_hash, peer_type, phone_number in peers:
            peer = _Peer(access_hash, peer_type, phone_number, now)
            self._peers[peer_id] = peer
            if peer_id in persist and self._persisted.get(peer_id) != peer.row:
                changed[peer_id] = peer.row
        if not changed:
            return
        stmt = pg_insert(TgPeer).values(
            [
                {
                    "account_id": self._account_id,
                    "id": peer_id,
                    "access_hash": access_hash,
                    "type": peer_type,
                    "phone_number": phone_number,
                }
                for peer_id, (access_hash, peer_type, phone_number) in changed.items()
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[TgPeer.account_id, TgPeer.id],
            set_={
                "access_hash": stmt.excluded.access_hash,
                "type": stmt.excluded.type,
                "phone_number": stmt.excluded.phone_number,
                "updated_at": func.now(),
            },
        )
        async with self._db.sessions() as session, session.begin():
            await session.execute(stmt)
        self._persisted.update(changed)

    async def update_usernames(self, usernames: Iterable[tuple[int, list[str | None]]]) -> None:
        for peer_id, names in usernames:
            kept = tuple(name for name in names if name is not None)
            if kept:
                self._usernames[peer_id] = kept
            else:
                self._usernames.pop(peer_id, None)

    async def get_update_states(self, ids: int | Iterable[int] | None = None) -> list[UpdateState]:
        if ids is None:
            states = list(self._states.values())
        else:
            wanted = {ids} if isinstance(ids, int) else set(ids)
            states = [state for state_id, state in self._states.items() if state_id in wanted]
        # По возрастанию даты, пустая — первой, как `ORDER BY date ASC` у SQLite.
        return sorted(states, key=lambda state: (state.date is not None, state.date or 0))

    async def set_update_state(self, update_state: UpdateState | Iterable[UpdateState]) -> None:
        states = [update_state] if isinstance(update_state, UpdateState) else update_state
        for state in states:
            old = self._states.get(state.id)
            self._states[state.id] = state if old is None else _merge(old, state)

    async def delete_update_state(self, state_id: int | Iterable[int]) -> None:
        for one in [state_id] if isinstance(state_id, int) else state_id:
            self._states.pop(one, None)

    @staticmethod
    def _input_peer(peer_id: int, peer: _Peer) -> raw.base.InputPeer:
        return cast("raw.base.InputPeer", get_input_peer(peer_id, peer.access_hash, peer.type))

    async def get_peer_by_id(self, peer_id: int) -> raw.base.InputPeer:
        peer = self._peers.get(peer_id)
        if peer is None:
            raise KeyError(f"ID not found: {peer_id}")
        return self._input_peer(peer_id, peer)

    async def get_peer_by_username(self, username: str) -> raw.base.InputPeer:
        # Самый свежий из пиров с этим username; чей пир давно не обновлялся — устарел.
        found = [
            (peer_id, self._peers[peer_id])
            for peer_id, names in self._usernames.items()
            if username in names and peer_id in self._peers
        ]
        if not found:
            raise KeyError(f"Username not found: {username}")
        peer_id, peer = max(found, key=lambda item: item[1].seen_at)
        if abs(time.time() - peer.seen_at) > self.USERNAME_TTL:
            raise KeyError(f"Username expired: {username}")
        return self._input_peer(peer_id, peer)

    async def get_peer_by_phone_number(self, phone_number: str) -> raw.base.InputPeer:
        for peer_id, peer in self._peers.items():
            if peer.phone_number == phone_number:
                return self._input_peer(peer_id, peer)
        raise KeyError(f"Phone number not found: {phone_number}")

    async def _field(self, name: str, value: Any) -> Any:
        """Чтение поля сессии; с `value` — запись, а если значение изменилось, то и в базу.
        `object` — «значения нет» (как у `SQLiteStorage`): `None` тоже значение."""
        if value is object:
            return self._fields[name]
        changed = self._fields[name] != value
        self._fields[name] = value
        if changed:
            await self._write_session()
        return None

    async def dc_id(self, value: int | type[object] | None = object) -> Any:
        return await self._field("dc_id", value)

    async def api_id(self, value: int | type[object] | None = object) -> Any:
        return await self._field("api_id", value)

    async def server_address(self, value: str | type[object] | None = object) -> Any:
        return await self._field("server_address", value)

    async def port(self, value: int | type[object] | None = object) -> Any:
        return await self._field("port", value)

    async def test_mode(self, value: bool | type[object] | None = object) -> Any:
        return await self._field("test_mode", value)

    async def auth_key(self, value: bytes | type[object] | None = object) -> Any:
        return await self._field("auth_key", value)

    async def date(self, value: int | type[object] | None = object) -> Any:
        return await self._field("date", value)

    async def user_id(self, value: int | type[object] | None = object) -> Any:
        return await self._field("user_id", value)

    async def is_bot(self, value: bool | type[object] | None = object) -> Any:
        return await self._field("is_bot", value)


async def import_session_file(path: Path, storage: PgSessionStorage, peer_ids: set[int]) -> bool:
    """Переносит файл сессии `*.session` в базу: поля сессии и пиры из `peer_ids` (в `storage`
    их сохраняют только пиры из его `peer_ids()` — наборы должны совпадать), затем файл
    переименовывается в `<имя>.session.migrated`. Нет файла — `False`."""
    if not await asyncio.to_thread(path.is_file):
        return False
    if path.suffix != SQLiteStorage.FILE_EXTENSION:
        raise ValueError(f"файл сессии должен называться *{SQLiteStorage.FILE_EXTENSION}: {path}")
    source = SQLiteStorage(path.stem, workdir=path.parent)
    # open/close у kurigram без аннотаций.
    await source.open()  # type: ignore[no-untyped-call]
    try:
        api_id, test_mode, is_bot = (
            await source.api_id(),
            await source.test_mode(),
            await source.is_bot(),
        )
        server_address, port = await source.server_address(), await source.port()
        dc_id, auth_key, user_id = (
            await source.dc_id(),
            await source.auth_key(),
            await source.user_id(),
        )
        marks = ", ".join("?" for _ in peer_ids)
        rows = source.conn.execute(
            f"SELECT id, access_hash, type, phone_number FROM peers WHERE id IN ({marks})",
            tuple(peer_ids),
        ).fetchall()
    finally:
        await source.close()  # type: ignore[no-untyped-call]
    await storage.api_id(api_id)
    await storage.test_mode(None if test_mode is None else bool(test_mode))
    await storage.is_bot(None if is_bot is None else bool(is_bot))
    await storage.server_address(server_address)
    await storage.port(port)
    # Каждое поле пишется в базу сразу; `user_id` — последним: пока его нет, сессия при сбое
    # читается как пустая, а не как недописанная.
    await storage.dc_id(dc_id)
    await storage.auth_key(auth_key)
    await storage.user_id(user_id)
    await storage.update_peers([(row[0], row[1] or 0, row[2], row[3]) for row in rows])
    await storage.save()
    await asyncio.to_thread(path.rename, path.with_name(path.name + ".migrated"))
    return True
