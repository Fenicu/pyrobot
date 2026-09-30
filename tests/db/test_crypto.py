import base64
import logging
import secrets

import pytest
from sqlalchemy import select

from app.db.base import Database
from app.db.crypto import SecretBox, SecretKeyError, Undecryptable, ensure_key, parse_key
from app.db.models import ServerMeta, TgChatMark, TgPeer, TgSession


def _b64(size: int) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(size)).decode()


def _box() -> SecretBox:
    return SecretBox(secrets.token_bytes(32))


def test_seal_open_roundtrip_and_format() -> None:
    box = _box()
    blob = box.seal(b"key", "auth_key", 1)
    # Байт версии, 12 байт nonce, шифротекст той же длины, что данные, и 16 байт тега.
    assert blob[0] == 1 and len(blob) == 1 + 12 + 3 + 16
    assert box.open(blob, "auth_key", 1) == b"key"
    assert box.open(box.seal(b"", "auth_key", 1), "auth_key", 1) == b""


def test_seal_uses_fresh_nonce() -> None:
    box = _box()
    assert box.seal(b"key", "auth_key", 1) != box.seal(b"key", "auth_key", 1)


def test_open_rejects_other_account_purpose_or_key() -> None:
    box = _box()
    blob = box.seal(b"key", "auth_key", 1)
    with pytest.raises(Undecryptable):
        box.open(blob, "auth_key", 2)
    with pytest.raises(Undecryptable):
        box.open(blob, "api_hash", 1)
    with pytest.raises(Undecryptable):
        _box().open(blob, "auth_key", 1)


def test_open_rejects_damaged_blob() -> None:
    box = _box()
    blob = box.seal(b"key", "auth_key", 1)
    flipped = blob[:-1] + bytes([blob[-1] ^ 1])
    for bad in (flipped, bytes([2]) + blob[1:], blob[:20], b"", b"\x01"):
        with pytest.raises(Undecryptable):
            box.open(bad, "auth_key", 1)


def test_secret_box_needs_256_bit_key() -> None:
    for size in (0, 16, 31, 33):
        with pytest.raises(ValueError, match="32"):
            SecretBox(b"k" * size)


def test_parse_key_accepts_32_bytes_urlsafe_base64() -> None:
    raw = secrets.token_bytes(32)
    assert parse_key(base64.urlsafe_b64encode(raw).decode()) == raw


@pytest.mark.parametrize(
    "value", [None, "", "short", _b64(31), _b64(33), "!" * 44, _b64(32).replace("=", "", 1) + "!"]
)
def test_parse_key_rejects_bad_values(value: str | None) -> None:
    with pytest.raises(SecretKeyError, match=r"secrets\.token_bytes\(32\)") as err:
        parse_key(value)
    assert "PYROBOT_SECRET_KEY" in str(err.value)


async def _check(db: Database) -> bytes:
    async with db.sessions() as session:
        value = await session.scalar(select(ServerMeta.value).where(ServerMeta.key == "key_check"))
    assert value is not None
    return value


async def _add_session(db: Database, box: SecretBox) -> None:
    async with db.sessions() as session, session.begin():
        session.add(
            TgSession(
                account_id=1, dc_id=2, date=1, auth_key=box.seal(b"auth-key-bytes", "auth_key", 1)
            )
        )
        session.add(TgPeer(account_id=1, id=10, access_hash=5, type="user"))
        session.add(TgChatMark(account_id=1, chat_id=-100, from_id=0, msg_id=42))


async def _rows(db: Database) -> tuple[list[int], list[int], list[int]]:
    async with db.sessions() as session:
        sessions = list(await session.scalars(select(TgSession.account_id)))
        peers = list(await session.scalars(select(TgPeer.id)))
        marks = list(await session.scalars(select(TgChatMark.msg_id)))
    return sessions, peers, marks


@pytest.mark.db
async def test_ensure_key_writes_check_once(clean_db: Database) -> None:
    box = _box()
    await ensure_key(clean_db, box, reset=False)
    check = await _check(clean_db)
    assert check[0] == 1
    # Запись расшифровывается ключом с назначением `key_check` и аккаунтом 0.
    box.open(check, "key_check", 0)
    # Следующий старт с тем же ключом ничего не пишет.
    await ensure_key(clean_db, box, reset=False)
    assert await _check(clean_db) == check


@pytest.mark.db
async def test_ensure_key_wrong_key_refuses_and_keeps_sessions(clean_db: Database) -> None:
    box = _box()
    await ensure_key(clean_db, box, reset=False)
    await _add_session(clean_db, box)
    check = await _check(clean_db)
    with pytest.raises(SecretKeyError, match="ключ не подходит к базе"):
        await ensure_key(clean_db, _box(), reset=False)
    assert await _rows(clean_db) == ([1], [10], [42])
    assert await _check(clean_db) == check


@pytest.mark.db
async def test_ensure_key_reset_drops_sessions_and_rewrites_check(
    clean_db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    old, new = _box(), _box()
    await ensure_key(clean_db, old, reset=False)
    await _add_session(clean_db, old)
    with caplog.at_level(logging.WARNING, logger="app.db.crypto"):
        await ensure_key(clean_db, new, reset=True)
    # Сессии и пиры удалены, отметки сверки истории (принадлежат журналу) остались.
    assert await _rows(clean_db) == ([], [], [42])
    new.open(await _check(clean_db), "key_check", 0)
    with pytest.raises(Undecryptable):
        old.open(await _check(clean_db), "key_check", 0)
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert "PYROBOT_SECRET_KEY_RESET" in caplog.text
    # Новый ключ теперь принимается и без флага.
    await ensure_key(clean_db, new, reset=False)


@pytest.mark.db
async def test_ensure_key_reset_with_right_key_does_nothing(
    clean_db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    box = _box()
    await ensure_key(clean_db, box, reset=False)
    await _add_session(clean_db, box)
    check = await _check(clean_db)
    with caplog.at_level(logging.WARNING, logger="app.db.crypto"):
        await ensure_key(clean_db, box, reset=True)
    assert await _rows(clean_db) == ([1], [10], [42])
    assert await _check(clean_db) == check
    assert caplog.records == []


@pytest.mark.db
async def test_ensure_key_reset_on_empty_base_just_writes_check(clean_db: Database) -> None:
    box = _box()
    await ensure_key(clean_db, box, reset=True)
    box.open(await _check(clean_db), "key_check", 0)
