from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, select

from app.db.base import Database
from app.db.models import (
    Account,
    ActionRow,
    DecisionRow,
    LedgerRow,
    MessageRow,
    MetricRow,
    NotificationRow,
    User,
)
from app.db.users import Role


@dataclass(frozen=True)
class AdminUserRow:
    id: int
    login: str
    role: Role
    created_at: datetime
    last_login_at: datetime | None
    accounts: int
    max_accounts: int
    disabled: bool
    disabled_reason: str | None
    deleting: bool


@dataclass(frozen=True)
class AdminAccountRow:
    id: int
    name: str
    owner_id: int | None
    owner_login: str | None
    status: str
    status_reason: str | None
    blocked: bool
    blocked_reason: str | None
    messages_1h: int
    actions_1h: int
    rows: dict[str, int]


class AdminReads:
    """Служебные выборки пользователей и аккаунтов для консоли владельца."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def users(self) -> list[AdminUserRow]:
        accounts_count = (
            select(func.count(Account.id))
            .where(Account.owner_id == User.id, Account.status != "deleting")
            .correlate(User)
            .scalar_subquery()
        )
        stmt = select(User, accounts_count.label("accounts_count")).order_by(User.id)
        async with self._db.sessions() as session:
            rows = (await session.execute(stmt)).all()
            return [
                AdminUserRow(
                    id=u.id,
                    login=u.login,
                    role=cast(Role, u.role),
                    created_at=u.created_at,
                    last_login_at=u.last_login_at,
                    accounts=int(cnt or 0),
                    max_accounts=u.max_accounts,
                    disabled=u.disabled_at is not None,
                    disabled_reason=u.disabled_reason,
                    deleting=u.deleting_at is not None,
                )
                for u, cnt in rows
            ]

    async def user(self, user_id: int) -> AdminUserRow | None:
        accounts_count = (
            select(func.count(Account.id))
            .where(Account.owner_id == User.id, Account.status != "deleting")
            .correlate(User)
            .scalar_subquery()
        )
        stmt = select(User, accounts_count.label("accounts_count")).where(User.id == user_id)
        async with self._db.sessions() as session:
            row = (await session.execute(stmt)).first()
            if row is None:
                return None
            u, cnt = row
            return AdminUserRow(
                id=u.id,
                login=u.login,
                role=cast(Role, u.role),
                created_at=u.created_at,
                last_login_at=u.last_login_at,
                accounts=int(cnt or 0),
                max_accounts=u.max_accounts,
                disabled=u.disabled_at is not None,
                disabled_reason=u.disabled_reason,
                deleting=u.deleting_at is not None,
            )

    async def accounts(self, now: datetime) -> list[AdminAccountRow]:
        async with self._db.sessions() as session:
            stmt = (
                select(Account, User.login)
                .outerjoin(User, User.id == Account.owner_id)
                .order_by(Account.id)
            )
            accounts = (await session.execute(stmt)).all()
            if not accounts:
                return []

            cutoff = now - timedelta(hours=1)

            msgs_1h_stmt = (
                select(MessageRow.account_id, func.count())
                .where(MessageRow.received_at >= cutoff)
                .group_by(MessageRow.account_id)
            )
            msgs_1h = dict((await session.execute(msgs_1h_stmt)).all())

            acts_1h_stmt = (
                select(ActionRow.account_id, func.count())
                .where(ActionRow.created_at >= cutoff)
                .group_by(ActionRow.account_id)
            )
            acts_1h = dict((await session.execute(acts_1h_stmt)).all())

            async def _counts(col: Any) -> dict[int, int]:
                s = select(col, func.count()).where(col.is_not(None)).group_by(col)
                return dict((await session.execute(s)).all())

            msgs_cnt = await _counts(MessageRow.account_id)
            acts_cnt = await _counts(ActionRow.account_id)
            decs_cnt = await _counts(DecisionRow.account_id)
            mets_cnt = await _counts(MetricRow.account_id)
            ledg_cnt = await _counts(LedgerRow.account_id)
            notif_cnt = await _counts(NotificationRow.account_id)

            return [
                AdminAccountRow(
                    id=acc.id,
                    name=acc.name,
                    owner_id=acc.owner_id,
                    owner_login=owner_login,
                    status=acc.status,
                    status_reason=acc.status_reason,
                    blocked=acc.blocked,
                    blocked_reason=acc.blocked_reason,
                    messages_1h=int(msgs_1h.get(acc.id, 0)),
                    actions_1h=int(acts_1h.get(acc.id, 0)),
                    rows={
                        "messages": int(msgs_cnt.get(acc.id, 0)),
                        "actions": int(acts_cnt.get(acc.id, 0)),
                        "decisions": int(decs_cnt.get(acc.id, 0)),
                        "metrics": int(mets_cnt.get(acc.id, 0)),
                        "ledger": int(ledg_cnt.get(acc.id, 0)),
                        "notifications": int(notif_cnt.get(acc.id, 0)),
                    },
                )
                for acc, owner_login in accounts
            ]

    async def account(self, account_id: int, now: datetime) -> AdminAccountRow | None:
        for acc in await self.accounts(now):
            if acc.id == account_id:
                return acc
        return None
