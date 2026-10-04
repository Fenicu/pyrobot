from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.security import dummy_hash, hash_password, verify_password
from app.db.base import Database
from app.db.models import RecoveryCodeRow, RecoveryRequestRow

_BASE32_ALPHABET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def new_secret() -> str:
    """10 символов base32 (без паддинга '=', upper-case), из 50 бит secrets.token_bytes."""
    return base64.b32encode(secrets.token_bytes(7))[:10].decode("ascii")


def format_code(row_id: int, secret: str) -> str:
    """Формат резервного кода: f'{row_id}-{secret[:5]}-{secret[5:]}'."""
    return f"{row_id}-{secret[:5]}-{secret[5:]}"


def parse_code(text: str) -> tuple[int, str] | None:
    """Парсит код: регистр и лишние дефисы/пробелы в секрете не важны."""
    stripped = text.strip()
    if "-" not in stripped:
        return None
    first, _, rest = stripped.partition("-")
    row_id_str = first.strip()
    if not row_id_str.isdigit():
        return None
    row_id = int(row_id_str)
    if row_id <= 0:
        return None
    secret_cleaned = rest.replace("-", "").replace(" ", "").upper()
    if len(secret_cleaned) != 10 or not secret_cleaned.isalnum():
        return None
    return row_id, secret_cleaned


class RecoveryCodes:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def issue(self, user_id: int) -> list[str]:
        """Генерирует 10 новых кодов восстановления, удаляя старые."""
        secrets_list = [new_secret() for _ in range(10)]
        hashes = [await hash_password(s) for s in secrets_list]

        async with self._db.sessions() as session, session.begin():
            await session.execute(
                delete(RecoveryCodeRow).where(RecoveryCodeRow.user_id == user_id)
            )
            stmt = (
                insert(RecoveryCodeRow)
                .values([{"user_id": user_id, "code_hash": h} for h in hashes])
                .returning(RecoveryCodeRow.id)
            )
            ids = list(await session.scalars(stmt))

        return [format_code(row_id, s) for row_id, s in zip(ids, secrets_list, strict=True)]

    async def use(self, user_id: int, code: str) -> bool:
        """Одноразовое использование кода восстановления с защитой от тайминг-атак."""
        parsed = parse_code(code)
        if parsed is None:
            await verify_password(await dummy_hash(), "dummy")
            return False

        row_id, secret = parsed
        async with self._db.sessions() as session, session.begin():
            row = await session.scalar(
                select(RecoveryCodeRow)
                .where(
                    RecoveryCodeRow.id == row_id,
                    RecoveryCodeRow.user_id == user_id,
                    RecoveryCodeRow.used_at.is_(None),
                )
                .with_for_update()
            )
            if row is None:
                await verify_password(await dummy_hash(), secret)
                return False

            valid = await verify_password(row.code_hash, secret)
            if valid:
                row.used_at = func.now()
                return True
            return False


class RecoveryRequests:
    def __init__(self, db: Database, key: bytes) -> None:
        self._db = db
        self._key = key

    def _hash_code(self, code: str) -> bytes:
        return hmac.new(self._key, code.encode("utf-8"), hashlib.sha256).digest()

    async def start(self, user_id: int) -> str:
        code = f"{secrets.randbelow(10**8):08d}"
        code_hash = self._hash_code(code)
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=10)

        stmt = (
            pg_insert(RecoveryRequestRow)
            .values(
                user_id=user_id,
                code_hash=code_hash,
                expires_at=expires_at,
                attempts=0,
                created_at=now,
            )
            .on_conflict_do_update(
                index_elements=[RecoveryRequestRow.user_id],
                set_={
                    "code_hash": code_hash,
                    "expires_at": expires_at,
                    "attempts": 0,
                    "created_at": now,
                },
            )
        )
        async with self._db.sessions() as session, session.begin():
            await session.execute(stmt)
        return code

    async def check(self, user_id: int, code: str) -> bool:
        expected_hash = self._hash_code(code)
        now = datetime.now(UTC)
        async with self._db.sessions() as session, session.begin():
            row = await session.scalar(
                select(RecoveryRequestRow)
                .where(RecoveryRequestRow.user_id == user_id)
                .with_for_update()
            )
            if row is None:
                return False

            row_expires = row.expires_at
            if row_expires.tzinfo is None:
                row_expires = row_expires.replace(tzinfo=UTC)
            if row_expires <= now:
                await session.execute(
                    delete(RecoveryRequestRow).where(RecoveryRequestRow.user_id == user_id)
                )
                return False

            if not hmac.compare_digest(row.code_hash, expected_hash):
                row.attempts += 1
                if row.attempts >= 5:
                    await session.execute(
                        delete(RecoveryRequestRow).where(RecoveryRequestRow.user_id == user_id)
                    )
                return False

            await session.execute(
                delete(RecoveryRequestRow).where(RecoveryRequestRow.user_id == user_id)
            )
            return True
