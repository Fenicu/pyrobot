from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Date, DateTime, Integer, Text, bindparam, cast, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, array

from app.db.accounts import AccountInfo, _info
from app.db.base import Database
from app.db.models import Account, SettingsRow, StateSnapshot
from app.db.overview_feed import (
    FEED_TYPES,
    MANUAL_CODES,
    FeedCursor,
    FeedEvent,
    FeedPageData,
    feed_after,
    feed_page,
)
from app.db.overview_search import SearchHit, SearchTarget, search_messages, search_targets
from app.engine.daily import BALANCE_KEYS, LEVEL_KEY, LedgerDays
from app.engine.gametime import day_start, tasks_day
from app.engine.settings import Settings
from app.engine.state.model import SCHEMA_VERSION, CharacterState, load_state

# Тяжёлые ключи снимка обзору не нужны: `applied` держит ключи итогов за две недели.
_HEAVY = ("applied", "activity_stats", "gadgets", "trips")


@dataclass(frozen=True, slots=True)
class AccountInput:
    """Аккаунт учётки для экранов обзора: настройки (при сбое чтения - умолчания) и снимок
    состояния; `state` None - снимка нет или у него другая версия схемы."""

    info: AccountInfo
    settings: Settings
    state: CharacterState | None


@dataclass(frozen=True, slots=True)
class TotalsSource:
    """Сырьё итогов периода одного аккаунта (свёртка - чистые функции `app.engine.daily`)."""

    account_id: int
    # Последнее значение ключа в каждые сутки МСК, начиная с суток перед окном.
    last: dict[str, dict[date, float]]
    # Последнее значение ключа до начала окна (любого возраста, но не старше порога хранения).
    before: dict[str, float]
    ledger: LedgerDays
    # Первый день журнала прихода (сутки самой ранней записи) или None.
    ledger_since: date | None


_METRIC_KEYS = (*BALANCE_KEYS, LEVEL_KEY)

# Последняя точка каждой пары (аккаунт, ключ) в каждых сутках: по одному спуску индекса
# ix_metrics_account_key_ts на тройку, а не чтение всех точек окна с сортировкой.
_DAY_VALUES = text(
    """
    SELECT a.id AS account_id, k.key, d.day, m.value
    FROM unnest(:ids) AS a(id)
    CROSS JOIN unnest(:keys) AS k(key)
    CROSS JOIN unnest(:days, :los, :his) AS d(day, lo, hi)
    CROSS JOIN LATERAL (
        SELECT value FROM metrics
        WHERE account_id = a.id AND key = k.key AND ts >= d.lo AND ts < d.hi
        ORDER BY ts DESC, id DESC
        LIMIT 1
    ) AS m
    """
).bindparams(
    bindparam("ids", type_=ARRAY(Integer)),
    bindparam("keys", type_=ARRAY(Text)),
    bindparam("days", type_=ARRAY(Date)),
    bindparam("los", type_=ARRAY(DateTime(timezone=True))),
    bindparam("his", type_=ARRAY(DateTime(timezone=True))),
)

_VALUE_BEFORE = text(
    """
    SELECT a.id AS account_id, k.key, m.value
    FROM unnest(:ids) AS a(id)
    CROSS JOIN unnest(:keys) AS k(key)
    CROSS JOIN LATERAL (
        SELECT value FROM metrics
        WHERE account_id = a.id AND key = k.key AND ts >= :floor AND ts < :start
        ORDER BY ts DESC, id DESC
        LIMIT 1
    ) AS m
    """
).bindparams(
    bindparam("ids", type_=ARRAY(Integer)),
    bindparam("keys", type_=ARRAY(Text)),
    bindparam("floor", type_=DateTime(timezone=True)),
    bindparam("start", type_=DateTime(timezone=True)),
)

# Журнал прихода, свёрнутый в SQL по (аккаунт, сутки, вид): число записей ('n') и суммы по ключам
# сумм ('a') и предметов ('i'); берутся только числовые значения. Три группировки с хеш-агрегатом,
# а не одна с разворотом каждой записи в строки: сортировка 1,9 млн развёрнутых строк за 30
# плотных суток на порядок дороже трёх проходов по индексу. `first_id` хранит порядок первого
# появления ключей, как при свёртке записей по порядку.
_LEDGER_FOLD = text(
    """
    SELECT account_id, day, kind, 'n' AS part, '' AS key, count(*)::numeric AS value,
           min(id) AS first_id
    FROM ledger
    WHERE account_id = ANY(:ids) AND day >= :first AND at < :now
    GROUP BY account_id, day, kind
    UNION ALL
    SELECT l.account_id, l.day, l.kind, 'a', kv.key, sum(kv.value::numeric), min(l.id)
    FROM ledger AS l CROSS JOIN LATERAL jsonb_each(l.amounts) AS kv
    WHERE l.account_id = ANY(:ids) AND l.day >= :first AND l.at < :now
      AND jsonb_typeof(kv.value) = 'number'
    GROUP BY l.account_id, l.day, l.kind, kv.key
    UNION ALL
    SELECT l.account_id, l.day, l.kind, 'i', kv.key, sum(kv.value::numeric), min(l.id)
    FROM ledger AS l CROSS JOIN LATERAL jsonb_each(l.items) AS kv
    WHERE l.account_id = ANY(:ids) AND l.day >= :first AND l.at < :now
      AND jsonb_typeof(kv.value) = 'number'
    GROUP BY l.account_id, l.day, l.kind, kv.key
    ORDER BY account_id, day, first_id, kind, part
    """
).bindparams(
    bindparam("ids", type_=ARRAY(Integer)),
    bindparam("first", type_=Date),
    bindparam("now", type_=DateTime(timezone=True)),
)

# Первая запись журнала каждого аккаунта: min по индексу ix_ledger_account_recorded - одна проба
# на аккаунт (GROUP BY прошёл бы по всем записям аккаунтов).
_LEDGER_SINCE = text(
    """
    SELECT a.id AS account_id,
           (SELECT min(recorded_at) FROM ledger WHERE account_id = a.id) AS since
    FROM unnest(:ids) AS a(id)
    """
).bindparams(bindparam("ids", type_=ARRAY(Integer)))


def _settings(data: dict[str, Any] | None) -> Settings:
    try:
        return Settings.model_validate(data or {})
    except ValidationError:
        return Settings()


def _state(data: dict[str, Any] | None) -> CharacterState | None:
    if not data or data.get("schema_version") != SCHEMA_VERSION:
        return None
    return load_state(data)


class OverviewReads:
    """Чтения раздела «Обзор»: всё в границах аккаунтов одной учётки."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def account_inputs(self, owner_id: int) -> list[AccountInput]:
        """Аккаунты учётки (кроме удаляемых) одним запросом: настройки и снимок состояния без
        тяжёлых ключей."""
        light = StateSnapshot.state.op("-", return_type=JSONB)(cast(array(_HEAVY), ARRAY(Text)))
        stmt = (
            select(Account, SettingsRow.data, light)
            .outerjoin(SettingsRow, SettingsRow.account_id == Account.id)
            .outerjoin(StateSnapshot, StateSnapshot.account_id == Account.id)
            .where(Account.owner_id == owner_id, Account.status != "deleting")
            .order_by(Account.id)
        )
        async with self._db.sessions() as session:
            rows = (await session.execute(stmt)).all()
        return [
            AccountInput(_info(row), _settings(data), _state(state)) for row, data, state in rows
        ]

    async def totals(
        self,
        owner_id: int,
        ids: Sequence[int],
        first: date,
        now: datetime,
        metrics_floor: datetime,
    ) -> list[TotalsSource]:
        """Сырьё итогов суток МСК с `first` до момента `now` по аккаунтам `ids` учётки (чужие,
        удаляемые и несуществующие молча отбрасываются), по возрастанию id.

        Всё в одной транзакции REPEATABLE READ: метрики и журнал приходят в базу по времени
        сообщения игры, а не по времени записи, и между запросами «настоящий» срез иначе
        разъехался бы. Точки метрик раньше `metrics_floor` не существуют (их стирает чистка), а
        записи с временем не меньше `now` в итог не входят."""
        owned = (
            select(Account.id)
            .where(Account.owner_id == owner_id, Account.status != "deleting")
            .where(Account.id.in_(list(ids)))
            .order_by(Account.id)
        )
        start = day_start(first)
        span = [first + timedelta(days=n) for n in range(-1, (tasks_day(now) - first).days + 1)]
        async with self._db.sessions() as session:
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            mine = list(await session.scalars(owned))
            if not mine:
                return []
            out = {i: TotalsSource(i, {}, {}, LedgerDays(), None) for i in mine}
            days = await session.execute(
                _DAY_VALUES,
                {
                    "ids": mine,
                    "keys": list(_METRIC_KEYS),
                    "days": span,
                    "los": [max(day_start(d), metrics_floor) for d in span],
                    "his": [min(day_start(d + timedelta(days=1)), now) for d in span],
                },
            )
            for account, key, day, value in days:
                out[account].last.setdefault(key, {})[day] = value
            before = await session.execute(
                _VALUE_BEFORE,
                {"ids": mine, "keys": list(_METRIC_KEYS), "floor": metrics_floor, "start": start},
            )
            for account, key, value in before:
                out[account].before[key] = value
            rows = await session.execute(_LEDGER_FOLD, {"ids": mine, "first": first, "now": now})
            counts: dict[tuple[int, date, str], int] = {}
            amounts: dict[tuple[int, date, str], dict[str, int]] = {}
            items: dict[tuple[int, date, str], dict[str, int]] = {}
            for account, day, kind, part, key, value, _ in rows:
                slot = (account, day, kind)
                if part == "n":
                    counts[slot] = int(value)
                else:
                    (amounts if part == "a" else items).setdefault(slot, {})[key] = int(value)
            for slot, count in counts.items():
                account, day, kind = slot
                out[account].ledger.add_group(
                    day, kind, count, amounts.get(slot, {}), items.get(slot, {})
                )
            since = await session.execute(_LEDGER_SINCE, {"ids": mine})
            return [
                TotalsSource(
                    s.account_id,
                    s.last,
                    s.before,
                    s.ledger,
                    tasks_day(started) if started is not None else None,
                )
                for account, started in since
                for s in [out[account]]
            ]

    async def feed_page(
        self,
        owner_id: int,
        ids: Sequence[int] | None,
        types: Sequence[str],
        *,
        limit: int,
        cursor: FeedCursor | None,
        ledger_floor: date,
    ) -> FeedPageData:
        """Страница ленты событий аккаунтов учётки (`ids` None - все), от новых к старым."""
        return await feed_page(
            self._db, owner_id, ids, types, limit=limit, cursor=cursor, ledger_floor=ledger_floor
        )

    async def feed_after(
        self,
        owner_id: int,
        ids: Sequence[int] | None,
        types: Sequence[str],
        *,
        after: tuple[int, int],
        limit: int,
        ledger_floor: date,
    ) -> FeedPageData:
        """Записи ленты, пришедшие после знака приёма `after` (id журнала, id уведомлений)."""
        return await feed_after(
            self._db, owner_id, ids, types, after=after, limit=limit, ledger_floor=ledger_floor
        )

    async def search_targets(self, owner_id: int, ids: Sequence[int] | None) -> list[SearchTarget]:
        """Аккаунты учётки (`ids` None - все) с чатом игры для поиска по сообщениям."""
        return await search_targets(self._db, owner_id, ids)

    async def search_messages(
        self, targets: Sequence[SearchTarget], q: str, limit: int
    ) -> list[SearchHit]:
        """Поиск `q` в сообщениях чатов игры `targets` (журнал `messages`), от новых к старым."""
        return await search_messages(self._db, targets, q, limit)


__all__ = [
    "FEED_TYPES",
    "MANUAL_CODES",
    "AccountInput",
    "FeedCursor",
    "FeedEvent",
    "FeedPageData",
    "OverviewReads",
    "SearchHit",
    "SearchTarget",
    "TotalsSource",
]
