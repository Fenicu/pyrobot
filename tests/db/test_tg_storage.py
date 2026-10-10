import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from app.db.base import Database
from app.db.crypto import SecretBox, Undecryptable
from app.db.models import Account, TgPeer, TgSession

if TYPE_CHECKING:
    from app.db.tg_storage import PgSessionStorage

# pyrogram и app.db.tg_storage импортируются лениво, внутри работающего цикла (как в
# tests/engine/kurigram_fakes.py): импорт на этапе сбора тестов создаёт event loop с
# DeprecationWarning.
pytestmark = pytest.mark.db

GAME = -1001109615116
SWINFO = 267519921
OTHER = -1002000000001
BOX = SecretBox(secrets.token_bytes(32))


def _pg(
    db: Database, peer_ids: set[int] | None = None, account_id: int = 1, box: SecretBox = BOX
) -> "PgSessionStorage":
    from app.db.tg_storage import PgSessionStorage

    return PgSessionStorage(db, account_id, box, lambda: peer_ids or set())


@pytest.fixture(params=["sqlite", "pg"])
async def storage(request: pytest.FixtureRequest, clean_db: Database) -> AsyncIterator[Any]:
    from pyrogram.storage import SQLiteStorage

    if request.param == "sqlite":
        opened: Any = SQLiteStorage("test", workdir=Path(), in_memory=True)
    else:
        opened = _pg(clean_db)
    await opened.open()
    yield opened
    await opened.close()


async def test_new_session_defaults(storage: Any) -> None:
    assert await storage.dc_id() == 2
    assert await storage.server_address() == "149.154.167.51" and await storage.port() == 443
    assert await storage.date() == 0
    for empty in (
        storage.api_id,
        storage.test_mode,
        storage.auth_key,
        storage.user_id,
        storage.is_bot,
    ):
        assert await empty() is None


async def test_session_fields_roundtrip(storage: Any) -> None:
    await storage.dc_id(4)
    await storage.api_id(12345)
    await storage.test_mode(True)
    await storage.auth_key(b"k" * 256)
    await storage.user_id(267519921)
    await storage.is_bot(False)
    await storage.server_address("149.154.167.91")
    await storage.port(80)
    assert await storage.dc_id() == 4 and await storage.auth_key() == b"k" * 256
    assert await storage.user_id() == 267519921 and await storage.api_id() == 12345
    assert await storage.test_mode() == 1 and await storage.is_bot() == 0
    assert await storage.server_address() == "149.154.167.91" and await storage.port() == 80
    # `None` — тоже значение: kurigram так сбрасывает вошедшего пользователя.
    await storage.user_id(None)
    assert await storage.user_id() is None


async def test_save_sets_date(storage: Any) -> None:
    await storage.save()
    assert abs(await storage.date() - time.time()) < 5


async def test_peers_in_memory(storage: Any) -> None:
    from pyrogram import raw

    await storage.update_peers([(-1001109615116, 42, "supergroup", None)])
    peer = await storage.get_peer_by_id(-1001109615116)
    assert isinstance(peer, raw.types.InputPeerChannel)
    assert peer.channel_id == 1109615116 and peer.access_hash == 42


async def test_peer_types(storage: Any) -> None:
    from pyrogram import raw

    await storage.update_peers([(7, 70, "user", None), (-8, 0, "group", None)])
    user, group = await storage.get_peer_by_id(7), await storage.get_peer_by_id(-8)
    assert isinstance(user, raw.types.InputPeerUser) and user.access_hash == 70
    assert isinstance(group, raw.types.InputPeerChat) and group.chat_id == 8


async def test_unknown_peer_raises_key_error(storage: Any) -> None:
    with pytest.raises(KeyError):
        await storage.get_peer_by_id(OTHER)
    with pytest.raises(KeyError):
        await storage.get_peer_by_username("nobody")
    with pytest.raises(KeyError):
        await storage.get_peer_by_phone_number("79990000000")


async def test_peer_is_replaced_by_update(storage: Any) -> None:
    from pyrogram import raw

    await storage.update_peers([(7, 70, "user", "79991112233")])
    await storage.update_peers([(7, 71, "user", None)])
    peer = await storage.get_peer_by_id(7)
    assert isinstance(peer, raw.types.InputPeerUser) and peer.access_hash == 71
    with pytest.raises(KeyError):
        await storage.get_peer_by_phone_number("79991112233")


async def test_peer_by_phone_number(storage: Any) -> None:
    from pyrogram import raw

    await storage.update_peers([(7, 70, "user", "79991112233")])
    peer = await storage.get_peer_by_phone_number("79991112233")
    assert isinstance(peer, raw.types.InputPeerUser) and peer.user_id == 7


async def test_peer_by_username(storage: Any) -> None:
    from pyrogram import raw

    await storage.update_peers([(7, 70, "user", None), (8, 80, "user", None)])
    await storage.update_usernames([(7, ["alice", None, "al"]), (8, ["bob"])])
    alice = await storage.get_peer_by_username("al")
    assert isinstance(alice, raw.types.InputPeerUser) and alice.user_id == 7
    # Новый список заменяет прежний, пустой — снимает все username.
    await storage.update_usernames([(7, ["alice2"]), (8, [None])])
    assert (await storage.get_peer_by_username("alice2")) is not None
    for gone in ("alice", "al", "bob"):
        with pytest.raises(KeyError):
            await storage.get_peer_by_username(gone)


async def test_stale_username_expires(storage: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    await storage.update_peers([(7, 70, "user", None)])
    await storage.update_usernames([(7, ["alice"])])
    assert await storage.get_peer_by_username("alice") is not None
    later = time.time() + 9 * 3600
    monkeypatch.setattr(time, "time", lambda: later)
    with pytest.raises(KeyError, match="expired"):
        await storage.get_peer_by_username("alice")


async def test_update_state_in_memory(storage: Any) -> None:
    from pyrogram.storage import UpdateState

    assert await storage.get_update_states() == []
    await storage.set_update_state(UpdateState(0, 10, 1, 100, 5))
    await storage.set_update_state([UpdateState(-1001, 20, None, 50, None)])
    assert await storage.get_update_states(0) == [UpdateState(0, 10, 1, 100, 5)]
    # По возрастанию даты.
    assert [s.id for s in await storage.get_update_states()] == [-1001, 0]
    assert await storage.get_update_states([-1001, 7]) == [UpdateState(-1001, 20, None, 50, None)]
    assert await storage.get_update_states([]) == []
    # Пустые поля нового состояния старые не затирают.
    await storage.set_update_state(UpdateState(0, 11, None, None, None))
    assert await storage.get_update_states(0) == [UpdateState(0, 11, 1, 100, 5)]
    await storage.delete_update_state(0)
    await storage.delete_update_state([-1001, 99])
    assert await storage.get_update_states() == []


async def test_export_session_string(storage: Any) -> None:
    await storage.dc_id(2)
    await storage.api_id(12345)
    await storage.test_mode(False)
    await storage.auth_key(b"k" * 256)
    await storage.user_id(267519921)
    await storage.is_bot(False)
    assert await storage.export_session_string()


async def _session_row(db: Database, account_id: int = 1) -> TgSession | None:
    async with db.sessions() as session:
        return await session.get(TgSession, account_id)


async def _peer_rows(db: Database, account_id: int = 1) -> dict[int, TgPeer]:
    async with db.sessions() as session:
        rows = await session.scalars(select(TgPeer).where(TgPeer.account_id == account_id))
        return {row.id: row for row in rows}


async def _add_account(db: Database, account_id: int = 2) -> None:
    async with db.sessions() as session, session.begin():
        session.add(Account(id=account_id, name=f"Аккаунт {account_id}"))


async def test_session_survives_new_object(clean_db: Database) -> None:
    first = _pg(clean_db)
    await first.open()
    await first.api_id(12345)
    await first.test_mode(False)
    await first.dc_id(4)
    await first.auth_key(b"k" * 256)
    await first.user_id(267519921)
    await first.is_bot(False)
    await first.server_address("149.154.167.91")
    await first.port(443)
    await first.save()
    second = _pg(clean_db)
    await second.open()
    assert await second.dc_id() == 4 and await second.auth_key() == b"k" * 256
    assert await second.user_id() == 267519921 and await second.api_id() == 12345
    assert await second.test_mode() is False and await second.is_bot() is False
    assert await second.server_address() == "149.154.167.91" and await second.port() == 443
    assert abs(await second.date() - time.time()) < 5


async def test_new_account_opens_empty(clean_db: Database) -> None:
    fresh = _pg(clean_db)
    await fresh.open()
    assert await fresh.auth_key() is None and await fresh.user_id() is None
    assert await _session_row(clean_db) is None


async def test_every_field_is_written_at_once(clean_db: Database) -> None:
    pg = _pg(clean_db)
    await pg.open()
    assert await _session_row(clean_db) is None
    steps: list[tuple[str, Any]] = [
        ("api_id", 12345),
        ("test_mode", False),
        ("dc_id", 4),
        ("server_address", "149.154.167.91"),
        ("port", 80),
        ("auth_key", b"k" * 256),
        ("user_id", 7),
        ("is_bot", False),
        ("date", 1700000000),
    ]
    for name, value in steps:
        await getattr(pg, name)(value)
        row = await _session_row(clean_db)
        assert row is not None
        stored = getattr(row, name)
        if name == "auth_key":
            stored = BOX.open(stored, "auth_key", 1)
        assert stored == value, name


async def test_unchanged_field_is_not_rewritten(clean_db: Database) -> None:
    pg = _pg(clean_db)
    await pg.open()
    await pg.port(80)
    row = await _session_row(clean_db)
    assert row is not None
    await pg.port(80)
    await pg.dc_id(2)
    again = await _session_row(clean_db)
    assert again is not None and again.updated_at == row.updated_at
    await pg.port(443)
    changed = await _session_row(clean_db)
    assert changed is not None and changed.port == 443 and changed.updated_at > row.updated_at


async def test_session_set_without_save_loads_complete(clean_db: Database) -> None:
    # Порядок вызовов kurigram при первом входе (load_session, затем sign_in): save() нет.
    pg = _pg(clean_db)
    await pg.open()
    await pg.api_id(12345)
    await pg.dc_id(2)
    await pg.server_address("149.154.167.51")
    await pg.port(443)
    await pg.date(0)
    await pg.test_mode(False)
    await pg.auth_key(b"k" * 256)
    await pg.user_id(None)
    await pg.is_bot(None)
    # Сбой тут: вход ещё не выполнен, сессия читается как пустая — kurigram создаст ключ заново.
    await pg.user_id(267519921)
    await pg.is_bot(False)
    again = _pg(clean_db)
    await again.open()
    assert await again.auth_key() == b"k" * 256 and await again.user_id() == 267519921
    assert await again.is_bot() is False and await again.test_mode() is False
    assert await again.api_id() == 12345 and await again.dc_id() == 2
    assert await again.server_address() == "149.154.167.51" and await again.port() == 443


async def test_only_configured_peers_persist(clean_db: Database) -> None:
    from pyrogram import raw

    pg = _pg(clean_db, {GAME, SWINFO})
    await pg.open()
    await pg.update_peers(
        [
            (GAME, 42, "supergroup", None),
            (SWINFO, 77, "user", "79991112233"),
            (OTHER, 99, "supergroup", None),
        ]
    )
    # Все три — в памяти этого объекта.
    assert await pg.get_peer_by_id(OTHER) is not None
    again = _pg(clean_db, {GAME, SWINFO})
    await again.open()
    game = await again.get_peer_by_id(GAME)
    assert isinstance(game, raw.types.InputPeerChannel) and game.access_hash == 42
    swinfo = await again.get_peer_by_id(SWINFO)
    assert isinstance(swinfo, raw.types.InputPeerUser) and swinfo.access_hash == 77
    assert (await again.get_peer_by_phone_number("79991112233")) is not None
    with pytest.raises(KeyError):
        await again.get_peer_by_id(OTHER)
    assert set(await _peer_rows(clean_db)) == {GAME, SWINFO}


async def test_peers_bounded_lru_keeps_configured(clean_db: Database) -> None:
    configured = {GAME}
    pg = _pg(clean_db, configured)
    pg.PEER_CAPACITY = 3
    await pg.open()
    await pg.update_peers([(GAME, 42, "supergroup", None)])
    await pg.update_peers([(1, 10, "user", None), (2, 20, "user", None)])
    await pg.update_usernames([(1, ["one"]), (2, ["two"]), (GAME, ["game"])])
    await pg.update_peers([(3, 30, "user", None)])
    # Вытеснен самый давний из ненастроенных; настроенный чат остаётся, хоть он и старше.
    with pytest.raises(KeyError):
        await pg.get_peer_by_id(1)
    with pytest.raises(KeyError):
        await pg.get_peer_by_username("one")
    assert 1 not in pg._usernames
    assert await pg.get_peer_by_id(GAME) is not None
    # Чтение освежает пир: следующим вытесняется 3, а не 2.
    assert await pg.get_peer_by_id(2) is not None
    await pg.update_peers([(4, 40, "user", None)])
    with pytest.raises(KeyError):
        await pg.get_peer_by_id(3)
    assert await pg.get_peer_by_id(2) is not None and await pg.get_peer_by_id(4) is not None
    # Чат, ставший настроенным, больше не вытесняется.
    configured.add(2)
    await pg.update_peers([(5, 50, "user", None), (6, 60, "user", None)])
    assert await pg.get_peer_by_id(2) is not None and await pg.get_peer_by_username("two")
    assert await pg.get_peer_by_id(GAME) is not None and await pg.get_peer_by_username("game")
    assert len(pg._peers) == 3


async def test_usernames_bounded(clean_db: Database) -> None:
    pg = _pg(clean_db, {GAME})
    pg.PEER_CAPACITY = 2
    await pg.open()
    await pg.update_usernames([(GAME, ["game"]), (1, ["one"]), (2, ["two"]), (3, ["three"])])
    assert set(pg._usernames) == {GAME, 3}


async def test_persisted_peer_is_rewritten_only_on_change(clean_db: Database) -> None:
    pg = _pg(clean_db, {GAME})
    await pg.open()
    await pg.update_peers([(GAME, 42, "supergroup", None)])
    written = (await _peer_rows(clean_db))[GAME].updated_at
    await pg.update_peers([(GAME, 42, "supergroup", None)])
    assert (await _peer_rows(clean_db))[GAME].updated_at == written
    await pg.update_peers([(GAME, 43, "supergroup", None)])
    row = (await _peer_rows(clean_db))[GAME]
    assert row.access_hash == 43 and row.updated_at > written
    # После перезагрузки то, что уже в базе, тоже не пишется повторно.
    again = _pg(clean_db, {GAME})
    await again.open()
    await again.update_peers([(GAME, 43, "supergroup", None)])
    assert (await _peer_rows(clean_db))[GAME].updated_at == row.updated_at


async def test_peers_are_per_account(clean_db: Database) -> None:
    await _add_account(clean_db)
    one, two = _pg(clean_db, {GAME}), _pg(clean_db, {GAME}, account_id=2)
    await one.update_peers([(GAME, 42, "supergroup", None)])
    await two.update_peers([(GAME, 43, "supergroup", None)])
    assert (await _peer_rows(clean_db, 1))[GAME].access_hash == 42
    assert (await _peer_rows(clean_db, 2))[GAME].access_hash == 43


async def test_update_state_not_persisted(clean_db: Database) -> None:
    from pyrogram.storage import UpdateState

    pg = _pg(clean_db, {GAME})
    await pg.open()
    await pg.update_peers([(GAME, 42, "supergroup", None)])
    await pg.update_usernames([(GAME, ["game"])])
    await pg.set_update_state(UpdateState(0, 10, 1, 100, 5))
    await pg.save()
    again = _pg(clean_db, {GAME})
    await again.open()
    assert await again.get_update_states() == []
    with pytest.raises(KeyError):
        await again.get_peer_by_username("game")


async def test_auth_key_encrypted_at_rest(clean_db: Database) -> None:
    pg = _pg(clean_db)
    await pg.open()
    await pg.auth_key(b"k" * 256)
    await pg.save()
    raw_row = await _session_row(clean_db)
    assert raw_row is not None and raw_row.auth_key is not None
    assert b"k" * 32 not in raw_row.auth_key
    assert BOX.open(raw_row.auth_key, "auth_key", 1) == b"k" * 256


async def test_foreign_blob_is_undecryptable(clean_db: Database) -> None:
    await _add_account(clean_db)
    one = _pg(clean_db)
    await one.open()
    await one.auth_key(b"k" * 256)
    row = await _session_row(clean_db)
    assert row is not None
    async with clean_db.sessions() as session, session.begin():
        session.add(TgSession(account_id=2, dc_id=2, date=0, auth_key=row.auth_key))
    with pytest.raises(Undecryptable):
        await _pg(clean_db, account_id=2).open()
    # Чужой ключ шифрования — то же самое.
    with pytest.raises(Undecryptable):
        await _pg(clean_db, box=SecretBox(secrets.token_bytes(32))).open()
    # Свой блоб с тем же ключом открывается.
    await one.open()


async def test_damaged_blob_is_undecryptable(clean_db: Database) -> None:
    pg = _pg(clean_db)
    await pg.open()
    await pg.auth_key(b"k" * 256)
    row = await _session_row(clean_db)
    assert row is not None and row.auth_key is not None
    async with clean_db.sessions() as session, session.begin():
        stored = await session.get(TgSession, 1)
        assert stored is not None
        stored.auth_key = row.auth_key[:-1] + bytes([row.auth_key[-1] ^ 1])
    with pytest.raises(Undecryptable):
        await pg.open()


async def test_close_then_open_same_object(clean_db: Database) -> None:
    from pyrogram.storage import UpdateState

    pg = _pg(clean_db, {GAME})
    await pg.open()
    await pg.auth_key(b"k" * 256)
    await pg.user_id(7)
    await pg.update_peers([(GAME, 42, "supergroup", None), (OTHER, 99, "supergroup", None)])
    await pg.set_update_state(UpdateState(0, 10, 1, 100, 5))
    await pg.save()
    await pg.close()
    await pg.open()
    assert await pg.auth_key() == b"k" * 256 and await pg.user_id() == 7
    assert await pg.get_peer_by_id(GAME) is not None
    # То, что жило только в памяти, после закрытия пропало.
    with pytest.raises(KeyError):
        await pg.get_peer_by_id(OTHER)
    assert await pg.get_update_states() == []
    await pg.close()


async def test_delete_removes_rows(clean_db: Database) -> None:
    await _add_account(clean_db)
    pg, other = _pg(clean_db, {GAME}), _pg(clean_db, {GAME}, account_id=2)
    for one in (pg, other):
        await one.open()
        await one.auth_key(b"k" * 256)
        await one.update_peers([(GAME, 42, "supergroup", None)])
    await pg.delete()
    assert await _session_row(clean_db, 1) is None and await _peer_rows(clean_db, 1) == {}
    # Аккаунт 2 не тронут.
    assert await _session_row(clean_db, 2) is not None and set(await _peer_rows(clean_db, 2)) == {
        GAME
    }
    # Объект после удаления — как новая сессия.
    assert await pg.auth_key() is None
    with pytest.raises(KeyError):
        await pg.get_peer_by_id(GAME)


def _names(directory: Path) -> list[str]:
    return sorted(item.name for item in directory.iterdir())


async def _make_session_file(workdir: Path) -> Path:
    from pyrogram.storage import SQLiteStorage

    source = SQLiteStorage("pyrobot", workdir=workdir)
    await source.open()
    try:
        await source.api_id(12345)
        await source.dc_id(4)
        await source.test_mode(False)
        await source.server_address("149.154.167.91")
        await source.port(443)
        await source.auth_key(b"k" * 256)
        await source.user_id(267519921)
        await source.is_bot(False)
        await source.update_peers(
            [
                (GAME, 42, "supergroup", None),
                (OTHER, 99, "supergroup", None),
                (7, 70, "user", None),
            ]
        )
        await source.save()
    finally:
        await source.close()
    return workdir / "pyrobot.session"


async def test_import_session_file(tmp_path: Path, clean_db: Database) -> None:
    from app.db.tg_storage import import_session_file

    path = await _make_session_file(tmp_path)
    pg = _pg(clean_db, {GAME})
    assert await import_session_file(path, pg, {GAME}) is True
    assert (tmp_path / "pyrobot.session.migrated").exists() and not path.exists()
    row = await _session_row(clean_db)
    assert row is not None and row.auth_key is not None
    assert BOX.open(row.auth_key, "auth_key", 1) == b"k" * 256
    assert (row.dc_id, row.user_id, row.api_id) == (4, 267519921, 12345)
    assert (row.test_mode, row.is_bot) == (False, False)
    assert (row.server_address, row.port) == ("149.154.167.91", 443)
    peers = await _peer_rows(clean_db)
    assert set(peers) == {GAME} and peers[GAME].access_hash == 42
    assert peers[GAME].type == "supergroup"
    # Новый объект открывает перенесённую сессию.
    again = _pg(clean_db, {GAME})
    await again.open()
    assert await again.user_id() == 267519921 and await again.get_peer_by_id(GAME) is not None
    with pytest.raises(KeyError):
        await again.get_peer_by_id(OTHER)


async def test_import_session_file_without_peers(tmp_path: Path, clean_db: Database) -> None:
    from app.db.tg_storage import import_session_file

    path = await _make_session_file(tmp_path)
    assert await import_session_file(path, _pg(clean_db), set()) is True
    assert await _peer_rows(clean_db) == {}
    assert (await _session_row(clean_db)) is not None


async def test_import_missing_file_is_false(tmp_path: Path, clean_db: Database) -> None:
    from app.db.tg_storage import import_session_file

    assert await import_session_file(tmp_path / "pyrobot.session", _pg(clean_db), {GAME}) is False
    assert _names(tmp_path) == []
    assert await _session_row(clean_db) is None


async def test_import_rejects_foreign_file_name(tmp_path: Path, clean_db: Database) -> None:
    from app.db.tg_storage import import_session_file

    stray = tmp_path / "pyrobot.db"
    stray.write_bytes(b"x")
    with pytest.raises(ValueError, match="session"):
        await import_session_file(stray, _pg(clean_db), {GAME})
    assert _names(tmp_path) == ["pyrobot.db"]


async def test_import_broken_file_raises_and_keeps_file(
    tmp_path: Path, clean_db: Database
) -> None:
    import sqlite3

    from app.db.tg_storage import import_session_file

    path = tmp_path / "pyrobot.session"
    path.write_bytes(b"not a database")
    # Соединение с файлом, который не база SQLite, закрывается: иначе ResourceWarning.
    with pytest.raises(sqlite3.DatabaseError):
        await import_session_file(path, _pg(clean_db), {GAME})
    assert _names(tmp_path) == ["pyrobot.session"]
    assert await _session_row(clean_db) is None
