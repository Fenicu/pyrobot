"""Поиск по сообщениям игры аккаунтов учётки: журнал `messages` (чтение для экрана «Поиск»)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import exists, func, select, tuple_
from sqlalchemy.orm import aliased

from app.db.accounts import AccountInfo, _info
from app.db.base import Database
from app.db.models import Account, MessageRow, SettingsRow
from app.engine.settings import Settings

_ESCAPE = "\\"


@dataclass(frozen=True, slots=True)
class SearchTarget:
    """Аккаунт учётки и его чат игры (`chats.game_chat_id`)."""

    info: AccountInfo
    chat_id: int


@dataclass(frozen=True, slots=True)
class SearchHit:
    account_id: int
    chat_id: int
    msg_id: int
    date: datetime
    outgoing: bool
    text: str | None
    markup: dict[str, Any] | None


def like_pattern(q: str) -> str:
    """Шаблон ILIKE «содержит `q`»: `%`, `_` и `\\` в запросе ищутся буквально."""
    escaped = (
        q.replace(_ESCAPE, _ESCAPE * 2).replace("%", f"{_ESCAPE}%").replace("_", f"{_ESCAPE}_")
    )
    return f"%{escaped}%"


def _game_chat(data: dict[str, Any] | None) -> int:
    try:
        return Settings.model_validate(data or {}).chats.game_chat_id
    except ValueError:
        return Settings().chats.game_chat_id


async def search_targets(
    db: Database, owner_id: int, ids: Sequence[int] | None
) -> list[SearchTarget]:
    """Аккаунты учётки (кроме удаляемых; `ids` None - все) с чатом игры, по возрастанию id; чужие
    и несуществующие id молча отбрасываются."""
    stmt = (
        select(Account, SettingsRow.data)
        .outerjoin(SettingsRow, SettingsRow.account_id == Account.id)
        .where(Account.owner_id == owner_id, Account.status != "deleting")
        .order_by(Account.id)
    )
    if ids is not None:
        stmt = stmt.where(Account.id.in_(list(ids)))
    async with db.sessions() as session:
        rows = (await session.execute(stmt)).all()
    return [SearchTarget(_info(account), _game_chat(data)) for account, data in rows]


async def search_messages(
    db: Database, targets: Sequence[SearchTarget], q: str, limit: int
) -> list[SearchHit]:
    """Сообщения чатов игры `targets`, где текст содержит `q` (без учёта регистра): не больше
    `limit` самых новых на аккаунт, общий список от новых к старым. Сообщение хранится по строке
    на ревизию - в выдачу идёт только последняя, и только если совпала именно она."""
    if not targets:
        return []
    m = aliased(MessageRow)
    newer = aliased(MessageRow)
    superseded = exists().where(
        newer.account_id == m.account_id,
        newer.chat_id == m.chat_id,
        newer.msg_id == m.msg_id,
        tuple_(newer.revision, newer.id) > tuple_(m.revision, m.id),
    )
    rank = (
        func.row_number()
        .over(partition_by=m.account_id, order_by=(m.date.desc(), m.id.desc()))
        .label("rank")
    )
    found = (
        select(m, rank)
        .where(
            tuple_(m.account_id, m.chat_id).in_([(t.info.id, t.chat_id) for t in targets]),
            m.text.ilike(like_pattern(q), escape=_ESCAPE),
            ~superseded,
        )
        .subquery()
    )
    stmt = (
        select(
            found.c.account_id,
            found.c.chat_id,
            found.c.msg_id,
            found.c.date,
            found.c.outgoing,
            found.c.text,
            found.c.markup,
        )
        .where(found.c.rank <= limit)
        .order_by(found.c.date.desc(), found.c.account_id, found.c.msg_id.desc())
    )
    async with db.sessions() as session:
        rows = (await session.execute(stmt)).all()
    return [SearchHit(*row) for row in rows]
