import base64
import logging
import os
import re

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import Database
from app.db.models import Account, ServerMeta, TgPeer, TgSession

log = logging.getLogger(__name__)

VERSION = 1
NONCE_LEN = 12
KEY_LEN = 32
# Известная строка под AAD `pyrobot:key_check:0`: по ней при старте видно, тот ли ключ у базы.
KEY_CHECK = "key_check"
_KEY_CHECK_TEXT = b"pyrobot key check"
_KEY_RE = re.compile(r"[A-Za-z0-9_-]{43}=?")

_KEY_HINT = (
    "PYROBOT_SECRET_KEY: нужен ключ 32 байта в urlsafe base64 — "
    'python -c "import secrets,base64;'
    'print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"'
)


class SecretKeyError(Exception):
    """Ключ шифрования не задан, испорчен или не подходит к базе; текст — для лога."""


class Undecryptable(Exception):
    """Блоб не расшифровался: другой ключ, другой аккаунт или назначение, повреждение."""


def parse_key(value: str | None) -> bytes:
    """Ключ из `PYROBOT_SECRET_KEY`: urlsafe base64 ровно 32 байта."""
    if not value or _KEY_RE.fullmatch(value) is None:
        raise SecretKeyError(_KEY_HINT)
    key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(key) != KEY_LEN:
        raise SecretKeyError(_KEY_HINT)
    return key


def derive_key(secret: bytes, purpose: str) -> bytes:
    """HKDF-SHA256: 32 байта, salt=None, info=f"pyrobot:{purpose}".encode()."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_LEN,
        salt=None,
        info=f"pyrobot:{purpose}".encode(),
    )
    return hkdf.derive(secret)


class SecretBox:
    """AES-256-GCM. Блоб: байт версии, 12 байт nonce, шифротекст с тегом. AAD —
    `pyrobot:<назначение>:<account_id>`: блоб нельзя подставить другому аккаунту или в другое
    поле."""

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_LEN:
            raise ValueError(f"ключ AES-256 — ровно {KEY_LEN} байта, получено {len(key)}")
        self._aead = AESGCM(key)

    @staticmethod
    def _aad(purpose: str, account_id: int) -> bytes:
        return f"pyrobot:{purpose}:{account_id}".encode()

    def seal(self, data: bytes, purpose: str, account_id: int) -> bytes:
        nonce = os.urandom(NONCE_LEN)
        body = self._aead.encrypt(nonce, data, self._aad(purpose, account_id))
        return bytes([VERSION]) + nonce + body

    def open(self, blob: bytes, purpose: str, account_id: int) -> bytes:
        if len(blob) < 1 + NONCE_LEN + 16 or blob[0] != VERSION:
            raise Undecryptable("неизвестный формат блоба")
        nonce, body = blob[1 : 1 + NONCE_LEN], blob[1 + NONCE_LEN :]
        try:
            return self._aead.decrypt(nonce, body, self._aad(purpose, account_id))
        except InvalidTag as exc:
            raise Undecryptable("блоб не расшифровался") from exc


async def ensure_key(db: Database, box: SecretBox, *, reset: bool) -> None:
    """Сверяет ключ с базой при старте: первый старт пишет `server_meta.key_check`, чужой ключ —
    `SecretKeyError`. `reset` — осознанный сброс: удаляет сессии и пиры Telegram и пишет
    `key_check` новым ключом; при подходящем ключе ничего не делает. Всё — одной транзакцией."""
    fresh = box.seal(_KEY_CHECK_TEXT, KEY_CHECK, 0)
    async with db.sessions() as session, session.begin():
        # Первый старт пишет запись сам; одновременный старт другого процесса не падает на
        # дубле ключа, а сверяет свой ключ с уже записанным.
        inserted = await session.scalar(
            pg_insert(ServerMeta)
            .values(key=KEY_CHECK, value=fresh)
            .on_conflict_do_nothing()
            .returning(ServerMeta.key)
        )
        if inserted is not None:
            return
        stored = await session.scalar(
            select(ServerMeta.value).where(ServerMeta.key == KEY_CHECK).with_for_update()
        )
        assert stored is not None
        try:
            box.open(stored, KEY_CHECK, 0)
            return
        except Undecryptable:
            if not reset:
                raise SecretKeyError("ключ не подходит к базе") from None
        dropped_sessions = await session.scalars(delete(TgSession).returning(TgSession.account_id))
        sessions = len(dropped_sessions.all())
        dropped_peers = await session.scalars(delete(TgPeer).returning(TgPeer.id))
        peers = len(dropped_peers.all())
        await session.execute(update(Account).values(tg_api_id=None, tg_api_hash=None))
        await session.execute(
            update(ServerMeta).where(ServerMeta.key == KEY_CHECK).values(value=fresh)
        )
    log.warning(
        "PYROBOT_SECRET_KEY_RESET: key mismatch; deleted Telegram sessions (%d) and peers (%d), "
        "cleared account Telegram applications; all accounts must log in again",
        sessions,
        peers,
    )
