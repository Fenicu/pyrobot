from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from app.db.base import Database
from app.db.models import InviteRow, RecoveryCodeRow, User
from app.db.users import LoginTaken, UserInfo
from app.db.users import _to_info as _to_user_info

if TYPE_CHECKING:
    from app.db.audit import Actor, AuditLog


@dataclass(frozen=True)
class InviteInfo:
    id: int
    created_by: int | None
    created_at: datetime
    expires_at: datetime
    max_accounts: int
    note: str | None
    used_at: datetime | None
    used_by: int | None
    revoked_at: datetime | None


class InviteNotFound(Exception):
    """Приглашение не найдено."""


class InviteGone(Exception):
    """Приглашение уже использовано, отозвано или истёк срок действия."""


def _to_info(r: InviteRow) -> InviteInfo:
    return InviteInfo(
        id=r.id,
        created_by=r.created_by,
        created_at=r.created_at,
        expires_at=r.expires_at,
        max_accounts=r.max_accounts,
        note=r.note,
        used_at=r.used_at,
        used_by=r.used_by,
        revoked_at=r.revoked_at,
    )


class AcceptResult(tuple[UserInfo, list[int]]):
    user: UserInfo
    code_ids: list[int]
    invite_id: int

    def __new__(cls, user: UserInfo, code_ids: list[int], invite_id: int) -> AcceptResult:
        obj = super().__new__(cls, (user, code_ids))
        obj.user = user
        obj.code_ids = code_ids
        obj.invite_id = invite_id
        return obj


class InviteRepo:
    def __init__(self, db: Database, audit: AuditLog) -> None:
        self._db = db
        self._audit = audit

    async def create(
        self,
        actor: Actor,
        *,
        ttl_h: int,
        max_accounts: int,
        note: str | None,
    ) -> tuple[str, InviteInfo]:
        if ttl_h <= 0 or ttl_h > 720:
            raise ValueError("ttl_h must be between 1 and 720 hours")
        if max_accounts <= 0:
            raise ValueError("max_accounts must be positive")

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).digest()
        expires_at = datetime.now(UTC) + timedelta(hours=ttl_h)

        async with self._db.sessions() as session, session.begin():
            row = InviteRow(
                token_hash=token_hash,
                created_by=actor.user_id,
                expires_at=expires_at,
                max_accounts=max_accounts,
                note=note,
            )
            session.add(row)
            await session.flush()
            await self._audit.write(
                actor,
                "invite_created",
                target_type="invite",
                target_id=row.id,
                details={"ttl_h": ttl_h, "max_accounts": max_accounts, "note": note},
                session=session,
            )
            info = _to_info(row)

        return token, info

    async def peek(self, token: str) -> InviteInfo:
        token_hash = hashlib.sha256(token.encode()).digest()
        async with self._db.sessions() as session:
            row = await session.scalar(select(InviteRow).where(InviteRow.token_hash == token_hash))
            if row is None:
                raise InviteNotFound
            now = await session.scalar(select(func.now()))
            assert now is not None
            if row.used_at is not None or row.revoked_at is not None or row.expires_at <= now:
                raise InviteGone
            return _to_info(row)

    async def accept(
        self,
        token: str,
        login: str,
        password_hash: str,
        code_hashes: list[str],
    ) -> AcceptResult:
        from app.db.audit import Actor

        token_hash = hashlib.sha256(token.encode()).digest()
        async with self._db.sessions() as session, session.begin():
            row = await session.scalar(
                select(InviteRow).where(InviteRow.token_hash == token_hash).with_for_update()
            )
            if row is None:
                raise InviteNotFound
            now = await session.scalar(select(func.now()))
            assert now is not None
            if row.used_at is not None or row.revoked_at is not None or row.expires_at <= now:
                raise InviteGone

            existing = await session.scalar(select(User.id).where(User.login == login))
            if existing is not None:
                raise LoginTaken

            new_user = User(
                login=login,
                password_hash=password_hash,
                role="user",
                max_accounts=row.max_accounts,
                invited_by=row.created_by,
            )
            session.add(new_user)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise LoginTaken from exc

            row.used_at = now
            row.used_by = new_user.id

            code_ids: list[int] = []
            if code_hashes:
                stmt = (
                    insert(RecoveryCodeRow)
                    .values([{"user_id": new_user.id, "code_hash": h} for h in code_hashes])
                    .returning(RecoveryCodeRow.id)
                )
                code_ids = list(await session.scalars(stmt))

            actor = Actor(new_user.id, new_user.login)
            await self._audit.write(
                actor,
                "user_registered",
                target_type="user",
                target_id=new_user.id,
                details={"invite_id": row.id},
                session=session,
            )
            return AcceptResult(_to_user_info(new_user), code_ids, row.id)

    async def revoke(self, invite_id: int, actor: Actor) -> None:
        async with self._db.sessions() as session, session.begin():
            row = await session.get(InviteRow, invite_id, with_for_update=True)
            if row is None:
                raise InviteNotFound
            now = await session.scalar(select(func.now()))
            assert now is not None
            if row.used_at is not None or row.revoked_at is not None or row.expires_at <= now:
                raise InviteGone
            row.revoked_at = now
            await self._audit.write(
                actor,
                "invite_revoked",
                target_type="invite",
                target_id=row.id,
                session=session,
            )

    async def unused(self) -> list[InviteInfo]:
        async with self._db.sessions() as session:
            rows = await session.scalars(
                select(InviteRow)
                .where(InviteRow.used_at.is_(None), InviteRow.revoked_at.is_(None))
                .order_by(InviteRow.id)
            )
            return [_to_info(r) for r in rows]
