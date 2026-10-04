from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, get_args

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Database
from app.db.models import AuditRow

if TYPE_CHECKING:
    from app.api.deps import SessionContext

AuditAction = Literal[
    "invite_created",
    "invite_revoked",
    "user_registered",
    "password_recovered",
    "recovery_codes_reissued",
    "password_set_by_cli",
    "user_limit_changed",
    "user_disabled",
    "user_enabled",
    "user_deleted",
    "account_blocked",
    "account_unblocked",
    "account_deleted",
    "server_settings_changed",
    "owner_promoted",
]

AUDIT_ACTIONS: frozenset[str] = frozenset(get_args(AuditAction))

AuditTargetType = Literal["user", "account", "invite", "server"]


@dataclass(frozen=True)
class Actor:
    user_id: int | None
    login: str

    @classmethod
    def of(cls, ctx: SessionContext) -> Actor:
        return cls(user_id=ctx.user_id, login=ctx.login)


CLI_ACTOR = Actor(None, "cli")


@dataclass(frozen=True)
class AuditEntry:
    id: int
    at: datetime
    actor_user_id: int | None
    actor_login: str
    action: str
    target_type: str | None
    target_id: int | None
    details: dict[str, Any]


class AuditLog:
    """Журнал действий администраторов и владельцев сервера (раздел 5.6 спеки)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def write(
        self,
        actor: Actor,
        action: AuditAction,
        *,
        target_type: str | None = None,
        target_id: int | None = None,
        details: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        if action not in AUDIT_ACTIONS:
            raise ValueError(f"unknown audit action: {action}")
        row = AuditRow(
            actor_user_id=actor.user_id,
            actor_login=actor.login,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details or {},
        )
        if session is not None:
            session.add(row)
        else:
            async with self._db.sessions() as s, s.begin():
                s.add(row)

    async def page(
        self,
        limit: int = 50,
        before: int | None = None,
    ) -> tuple[list[AuditEntry], int | None]:
        query = select(AuditRow).order_by(desc(AuditRow.id)).limit(limit + 1)
        if before is not None:
            query = query.where(AuditRow.id < before)
        async with self._db.sessions() as session:
            rows = list(await session.scalars(query))
        page_rows = rows[:limit]
        next_before = page_rows[-1].id if len(rows) > limit and page_rows else None
        entries = [
            AuditEntry(
                id=r.id,
                at=r.at,
                actor_user_id=r.actor_user_id,
                actor_login=r.actor_login,
                action=r.action,
                target_type=r.target_type,
                target_id=r.target_id,
                details=r.details,
            )
            for r in page_rows
        ]
        return entries, next_before
