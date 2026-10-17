"""Лента обзора: журнал прихода и уведомления всех аккаунтов учётки одним списком."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import DateTime, Integer, Text, bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Database
from app.db.models import Account

# Типы ленты. levelup, battle, factory, metro - виды журнала прихода с тем же именем.
FEED_TYPES = ("levelup", "battle", "factory", "metro", "lottery", "alert", "manual")
_LEDGER_KINDS = {
    "levelup": ("levelup",),
    "battle": ("battle",),
    "factory": ("factory",),
    "metro": ("metro",),
    "lottery": ("lottery_win", "lottery_skills"),
}
# Вмешательства оператора: коды пишет `EngineFacade._audit` уведомлениями уровня info.
MANUAL_CODES = ("engine_paused", "engine_resumed", "manual_control_on", "manual_control_off")
ALERT_LEVELS = ("warn", "error")

# Ранг источника в порядке «время, ранг, id» (как в `DbReads.feed`): при равном времени
# уведомления идут раньше записей журнала.
LEDGER_RANK, NOTE_RANK = 0, 1


@dataclass(frozen=True, slots=True)
class FeedCursor:
    """Ключ последней записи страницы: следующая страница строго старше."""

    at: datetime
    rank: int
    id: int


@dataclass(frozen=True, slots=True)
class FeedEvent:
    # `l` - журнал прихода, `n` - уведомление; id в таблицах разные, ключ - пара.
    source: str
    id: int
    at: datetime
    account_id: int
    type: str
    kind: str
    level: str | None
    text: str | None
    amounts: dict[str, int] | None
    items: dict[str, int] | None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def rank(self) -> int:
        return LEDGER_RANK if self.source == "l" else NOTE_RANK

    @property
    def order(self) -> tuple[datetime, int, int]:
        return (self.at, self.rank, self.id)


@dataclass(frozen=True, slots=True)
class FeedPageData:
    events: list[FeedEvent]
    # Есть ещё записи за страницей: старее (страница по курсору) или новее знака (приём).
    has_more: bool
    # Знак приёма (максимальные id журнала и уведомлений): у первой страницы и у приёма,
    # у страницы по курсору его нет - клиент держит знак первой страницы.
    watermark: tuple[int, int] | None


def _numbers(raw: dict[str, Any]) -> dict[str, int]:
    return {
        k: int(v) for k, v in raw.items() if isinstance(v, int | float) and not isinstance(v, bool)
    }


# Параметры, которым нужен явный тип (массивы и время); остальные asyncpg выводит сам.
_BIND_TYPES: dict[str, Any] = {
    "ids": ARRAY(Integer),
    "kinds": ARRAY(Text),
    "levels": ARRAY(Text),
    "manual": ARRAY(Text),
    "c_at": DateTime(timezone=True),
}


async def _run(session: AsyncSession, sql: str, params: dict[str, Any]) -> Any:
    stmt = text(sql).bindparams(
        *(bindparam(n, type_=_BIND_TYPES[n]) for n in params if n in _BIND_TYPES)
    )
    return await session.execute(stmt, params)


@dataclass(frozen=True, slots=True)
class _Selection:
    """Выбранные типы как условия к двум таблицам."""

    kinds: list[str]
    # Условие к `notifications` и его параметры; None - уведомления не выбраны.
    note_cond: str | None
    note_params: dict[str, Any]


def _select(types: Sequence[str]) -> _Selection:
    parts: list[str] = []
    params: dict[str, Any] = {}
    if "alert" in types:
        parts.append("level = ANY(:levels)")
        params["levels"] = list(ALERT_LEVELS)
    if "manual" in types:
        parts.append("code = ANY(:manual)")
        params["manual"] = list(MANUAL_CODES)
    cond = None if not parts else parts[0] if len(parts) == 1 else f"({' OR '.join(parts)})"
    kinds = [k for t in types if t in _LEDGER_KINDS for k in _LEDGER_KINDS[t]]
    return _Selection(kinds, cond, params)


def _cursor_cond(rank: int, at: str, ident: str, cursor: FeedCursor | None) -> str:
    """(at, rank, id) строго меньше курсора при постоянном ранге таблицы."""
    if cursor is None:
        return "true"
    if rank < cursor.rank:
        return f"{at} <= :c_at"
    if rank > cursor.rank:
        return f"{at} < :c_at"
    return f"({at} < :c_at OR ({at} = :c_at AND {ident} < :c_id))"


def _ledger_event(row: Any) -> FeedEvent:
    kind_type = "lottery" if row.kind in _LEDGER_KINDS["lottery"] else row.kind
    return FeedEvent(
        source="l",
        id=row.id,
        at=row.at,
        account_id=row.account_id,
        type=kind_type,
        kind=row.kind,
        level=None,
        text=None,
        amounts=_numbers(row.amounts),
        items=_numbers(row.items),
    )


def _note_event(row: Any) -> FeedEvent:
    return FeedEvent(
        source="n",
        id=row.id,
        at=row.created_at,
        account_id=row.account_id,
        type="manual" if row.code in MANUAL_CODES else "alert",
        kind=row.code,
        level=row.level,
        text=row.text,
        amounts=None,
        items=None,
    )


async def _owned(session: AsyncSession, owner_id: int, ids: Sequence[int] | None) -> list[int]:
    """Аккаунты учётки (кроме удаляемых) из `ids`; чужие и несуществующие молча отбрасываются."""
    stmt = select(Account.id).where(Account.owner_id == owner_id, Account.status != "deleting")
    if ids is not None:
        stmt = stmt.where(Account.id.in_(list(ids)))
    return list(await session.scalars(stmt.order_by(Account.id)))


async def _snapshot(session: AsyncSession) -> None:
    await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})


_LEDGER_COLS = "id, account_id, at, kind, amounts, items"
_NOTE_COLS = "id, account_id, created_at, level, code, text"


async def feed_page(
    db: Database,
    owner_id: int,
    ids: Sequence[int] | None,
    types: Sequence[str],
    *,
    limit: int,
    cursor: FeedCursor | None,
    ledger_floor: date,
) -> FeedPageData:
    """До `limit` записей от новых к старым по `(время, ранг, id)`; у первой страницы (без
    курсора) - знак приёма. Журнал и уведомления берутся по `limit + 1` каждый и сливаются в
    Python (как `DbReads.feed`); журнал - по одному спуску индекса ix_ledger_account_at на
    аккаунт, а не сортировкой всех записей учётки.

    Одна транзакция REPEATABLE READ: знак приёма и страница видят одни и те же данные, поэтому
    каждая запись страницы не новее знака."""
    sel = _select(types)
    events: list[FeedEvent] = []
    async with db.sessions() as session:
        await _snapshot(session)
        mine = await _owned(session, owner_id, ids)
        if not mine:
            return FeedPageData([], False, None if cursor else (0, 0))
        cur: dict[str, Any] = {"c_at": cursor.at, "c_id": cursor.id} if cursor is not None else {}
        n = limit + 1
        if sel.kinds:
            where = _cursor_cond(LEDGER_RANK, "at", "id", cursor)
            rows = await _run(
                session,
                f"""SELECT l.id, l.account_id, l.at, l.kind, l.amounts, l.items
                FROM unnest(:ids) AS a(id)
                CROSS JOIN LATERAL (
                    SELECT {_LEDGER_COLS} FROM ledger
                    WHERE account_id = a.id AND day >= :floor AND kind = ANY(:kinds) AND {where}
                    ORDER BY at DESC, id DESC LIMIT :n
                ) AS l
                ORDER BY l.at DESC, l.id DESC LIMIT :n""",
                {"ids": mine, "kinds": sel.kinds, "floor": ledger_floor, "n": n, **cur},
            )
            events.extend(_ledger_event(r) for r in rows)
        if sel.note_cond is not None:
            where = _cursor_cond(NOTE_RANK, "created_at", "id", cursor)
            rows = await _run(
                session,
                f"""SELECT n.id, n.account_id, n.created_at, n.level, n.code, n.text
                FROM unnest(:ids) AS a(id)
                CROSS JOIN LATERAL (
                    SELECT {_NOTE_COLS} FROM notifications
                    WHERE account_id = a.id AND {sel.note_cond} AND {where}
                    ORDER BY created_at DESC, id DESC LIMIT :n
                ) AS n
                ORDER BY n.created_at DESC, n.id DESC LIMIT :n""",
                {"ids": mine, "n": n, **sel.note_params, **cur},
            )
            events.extend(_note_event(r) for r in rows)
        mark = None if cursor is not None else await _watermark(session, mine, sel, ledger_floor)
    events.sort(key=lambda e: e.order, reverse=True)
    return FeedPageData(events[:limit], len(events) > limit, mark)


async def _watermark(
    session: AsyncSession, mine: list[int], sel: _Selection, floor: date
) -> tuple[int, int]:
    """Максимальные id выбранных записей журнала и уведомлений; нет записей или источник не
    выбран - 0."""
    ledger = 0
    if sel.kinds:
        ledger = await _max(
            session,
            "SELECT max(id) FROM ledger "
            "WHERE account_id = ANY(:ids) AND day >= :floor AND kind = ANY(:kinds)",
            {"ids": mine, "kinds": sel.kinds, "floor": floor},
        )
    notes = 0
    if sel.note_cond is not None:
        notes = await _max(
            session,
            f"SELECT max(id) FROM notifications WHERE account_id = ANY(:ids) AND {sel.note_cond}",
            {"ids": mine, **sel.note_params},
        )
    return ledger, notes


async def _max(session: AsyncSession, sql: str, params: dict[str, Any]) -> int:
    return int((await _run(session, sql, params)).scalar() or 0)


async def feed_after(
    db: Database,
    owner_id: int,
    ids: Sequence[int] | None,
    types: Sequence[str],
    *,
    after: tuple[int, int],
    limit: int,
    ledger_floor: date,
) -> FeedPageData:
    """Записи с `id` выше знака приёма `after` (журнал, уведомления), по возрастанию id внутри
    источника, не больше `limit`; новый знак - id последних отданных записей (у источника без
    новых записей прежний). Порция - начало списка новых записей каждого источника: то, что
    не поместилось, достанется следующему запросу (`has_more`), пропуска нет."""
    sel = _select(types)
    ledger: deque[FeedEvent] = deque()
    notes: deque[FeedEvent] = deque()
    async with db.sessions() as session:
        await _snapshot(session)
        mine = await _owned(session, owner_id, ids)
        n = limit + 1
        if mine and sel.kinds:
            rows = await _run(
                session,
                f"""SELECT {_LEDGER_COLS} FROM ledger
                WHERE account_id = ANY(:ids) AND day >= :floor AND kind = ANY(:kinds)
                    AND id > :after
                ORDER BY id LIMIT :n""",
                {
                    "ids": mine,
                    "kinds": sel.kinds,
                    "floor": ledger_floor,
                    "after": after[0],
                    "n": n,
                },
            )
            ledger.extend(_ledger_event(r) for r in rows)
        if mine and sel.note_cond is not None:
            rows = await _run(
                session,
                f"""SELECT {_NOTE_COLS} FROM notifications
                WHERE account_id = ANY(:ids) AND {sel.note_cond} AND id > :after
                ORDER BY id LIMIT :n""",
                {"ids": mine, "after": after[1], "n": n, **sel.note_params},
            )
            notes.extend(_note_event(r) for r in rows)
    taken: list[FeedEvent] = []
    marks = list(after)
    while len(taken) < limit and (ledger or notes):
        # Берётся начало списка одного из источников, поэтому отданные записи источника - его
        # префикс, а знак (id последней отданной) не перескакивает через неотданное.
        source = notes if not ledger or (notes and notes[0].order < ledger[0].order) else ledger
        event = source.popleft()
        taken.append(event)
        marks[0 if event.source == "l" else 1] = event.id
    return FeedPageData(taken, bool(ledger or notes), (marks[0], marks[1]))
