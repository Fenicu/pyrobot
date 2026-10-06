import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Date, Select, and_, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on

from app.db.accounts import engine_section
from app.db.base import Database
from app.db.models import (
    ActionRow,
    DecisionRow,
    LedgerRow,
    MessageRow,
    MetricRow,
    MetroRunRow,
    NotificationRow,
    ScenarioRunRow,
    SettingsHistory,
    SettingsRow,
    StateSnapshot,
    UnrecognizedRow,
)
from app.engine.daily import LedgerEntry
from app.engine.gametime import day_start, tasks_day
from app.engine.settings import EngineSection, Settings, settings_diff, stored_values

log = logging.getLogger(__name__)

# Порядок типов записей на одном моменте: сообщение, потом действие, потом решение.
FEED_TYPES = ("message", "action", "decision")
FeedRow = MessageRow | ActionRow | DecisionRow
_FEED_MODELS: dict[str, Any] = {
    "message": (MessageRow, MessageRow.received_at),
    "action": (ActionRow, ActionRow.created_at),
    "decision": (DecisionRow, DecisionRow.at),
}


@dataclass(frozen=True, slots=True)
class FeedKey:
    """Позиция записи в ленте: момент, ранг типа, id — лента идёт по убыванию ключа."""

    at: datetime
    rank: int
    id: int


@dataclass(frozen=True, slots=True)
class FeedFilter:
    types: tuple[str, ...] = FEED_TYPES
    since: datetime | None = None
    until: datetime | None = None
    chat_id: int | None = None
    status: str | None = None
    source: str | None = None

    def narrowed(self) -> tuple[str, ...]:
        types = self.types
        # Статус и источник есть только у действий, чат — у сообщений и действий.
        if self.status is not None or self.source is not None:
            types = tuple(t for t in types if t == "action")
        if self.chat_id is not None:
            types = tuple(t for t in types if t != "decision")
        return types


@dataclass(frozen=True, slots=True)
class FeedItem:
    type: str
    key: FeedKey
    row: FeedRow


@dataclass(frozen=True, slots=True)
class MetricPoint:
    id: int
    ts: datetime
    key: str
    value: float


@dataclass(frozen=True, slots=True)
class UnrecognizedItem:
    row: UnrecognizedRow
    text: str | None


@dataclass(frozen=True, slots=True)
class SettingsVersion:
    version: int
    changed_by: str
    changed_at: datetime
    changes: dict[str, list[Any]]


@dataclass(frozen=True, slots=True)
class UpgradeProgress:
    """Попытки заточки слота по журналу прихода: удачные, провалы, потрачено улучшений по видам."""

    attempts: int
    ok: int
    fail: int
    spent: dict[str, int]


_UPGRADE_KINDS = ("white", "blue", "red")


class DbReads:
    """Выборки для админки: журнал, справочное, история настроек (только чтение и ack)."""

    def __init__(self, db: Database, account_id: int) -> None:
        self._db = db
        self._account_id = account_id

    async def feed(self, flt: FeedFilter, limit: int, after: FeedKey | None) -> list[FeedItem]:
        """До `limit` записей ленты старше `after`, от новых к старым (слияние трёх таблиц)."""
        items: list[FeedItem] = []
        async with self._db.sessions() as session:
            for kind in flt.narrowed():
                rank = FEED_TYPES.index(kind)
                model, at = _FEED_MODELS[kind]
                query: Select[Any] = (
                    select(model)
                    .where(model.account_id == self._account_id)
                    .order_by(at.desc(), model.id.desc())
                    .limit(limit)
                )
                query = _feed_where(query, model, at, rank, flt, after)
                for row in await session.scalars(query):
                    moment = getattr(row, at.key)
                    items.append(FeedItem(kind, FeedKey(moment, rank, row.id), row))
        items.sort(key=lambda i: (i.key.at, i.key.rank, i.key.id), reverse=True)
        return items[:limit]

    async def decision_runs(self, decision_ids: Sequence[int]) -> dict[int, int]:
        """Запуск, начатый решением: id решения → id его первого запуска (решения без запуска — нет
        в ответе). Для ленты: по нему админка собирает решение и шаги запуска в одну строку."""
        if not decision_ids:
            return {}
        async with self._db.sessions() as session:
            rows = await session.execute(
                select(ScenarioRunRow.decision_id, func.min(ScenarioRunRow.id))
                .where(
                    ScenarioRunRow.account_id == self._account_id,
                    ScenarioRunRow.decision_id.in_(decision_ids),
                )
                .group_by(ScenarioRunRow.decision_id)
            )
            return {int(decision): int(run) for decision, run in rows if decision is not None}

    async def decision(self, decision_id: int) -> tuple[DecisionRow, list[ScenarioRunRow]] | None:
        async with self._db.sessions() as session:
            row = await session.get(DecisionRow, decision_id)
            if row is None or row.account_id != self._account_id:
                return None
            runs = await session.scalars(
                select(ScenarioRunRow)
                .where(ScenarioRunRow.decision_id == decision_id)
                .order_by(ScenarioRunRow.id)
            )
            return row, list(runs)

    async def action(self, action_id: int) -> ActionRow | None:
        async with self._db.sessions() as session:
            row = await session.get(ActionRow, action_id)
        return row if row is not None and row.account_id == self._account_id else None

    async def action_by_key(self, key: str) -> ActionRow | None:
        async with self._db.sessions() as session:
            return await session.scalar(
                select(ActionRow).where(
                    ActionRow.account_id == self._account_id, ActionRow.idempotency_key == key
                )
            )

    async def scenario_run(self, run_id: int) -> tuple[ScenarioRunRow, int | None] | None:
        async with self._db.sessions() as session:
            row = await session.get(ScenarioRunRow, run_id)
            if row is None or row.account_id != self._account_id:
                return None
            metro = await session.scalar(
                select(MetroRunRow.id)
                .where(MetroRunRow.scenario_run_id == run_id)
                .order_by(MetroRunRow.id.desc())
                .limit(1)
            )
            return row, metro

    async def scenario_runs(
        self, *, manual: bool | None, scenario: str | None, limit: int, before: int | None
    ) -> list[ScenarioRunRow]:
        """Запуски от новых к старым; `manual` — только ручные (с `requested_by`) или только
        плановые."""
        query = (
            select(ScenarioRunRow)
            .where(ScenarioRunRow.account_id == self._account_id)
            .order_by(ScenarioRunRow.id.desc())
            .limit(limit)
        )
        if manual is not None:
            by = ScenarioRunRow.requested_by
            query = query.where(by.is_not(None) if manual else by.is_(None))
        if scenario is not None:
            query = query.where(ScenarioRunRow.scenario == scenario)
        if before is not None:
            query = query.where(ScenarioRunRow.id < before)
        async with self._db.sessions() as session:
            return list(await session.scalars(query))

    async def run_actions(self, run_id: int) -> list[ActionRow]:
        """Действия шагов запуска в порядке создания."""
        async with self._db.sessions() as session:
            rows = await session.scalars(
                select(ActionRow)
                .where(
                    ActionRow.account_id == self._account_id,
                    ActionRow.scenario_run_id == run_id,
                )
                .order_by(ActionRow.id)
            )
            return list(rows)

    async def metrics(
        self,
        keys: Sequence[str],
        since: datetime,
        until: datetime,
        limit: int,
        after: tuple[datetime, int] | None,
    ) -> list[MetricPoint]:
        """Точки окна `[since, until)` по возрастанию `(ts, id)`, строго после `after`."""
        query = (
            select(MetricRow)
            .where(
                MetricRow.account_id == self._account_id,
                MetricRow.key.in_(keys),
                MetricRow.ts >= since,
                MetricRow.ts < until,
            )
            .order_by(MetricRow.ts, MetricRow.id)
            .limit(limit)
        )
        if after is not None:
            ts, ident = after
            query = query.where(
                or_(MetricRow.ts > ts, and_(MetricRow.ts == ts, MetricRow.id > ident))
            )
        async with self._db.sessions() as session:
            rows = await session.scalars(query)
            return [MetricPoint(r.id, r.ts, r.key, r.value) for r in rows]

    async def metrics_before(
        self, keys: Sequence[str], moment: datetime
    ) -> dict[str, tuple[datetime, float]]:
        """Последнее значение каждого ключа до `moment` — значение на начало окна."""
        query = (
            select(MetricRow.key, MetricRow.ts, MetricRow.value)
            .where(
                MetricRow.account_id == self._account_id,
                MetricRow.key.in_(keys),
                MetricRow.ts < moment,
            )
            .order_by(MetricRow.key, MetricRow.ts.desc(), MetricRow.id.desc())
            .ext(distinct_on(MetricRow.key))
        )
        async with self._db.sessions() as session:
            rows = await session.execute(query)
            return {key: (ts, value) for key, ts, value in rows.all()}

    async def runs_done(
        self, scenarios: Sequence[str], start: datetime, end: datetime
    ) -> list[tuple[datetime, str]]:
        """Удачные запуски этих сценариев, закончившиеся в `[start, end)`: момент конца и сценарий,
        по времени — метки событий на графиках метрик."""
        query = (
            select(ScenarioRunRow.finished_at, ScenarioRunRow.scenario)
            .where(
                ScenarioRunRow.account_id == self._account_id,
                ScenarioRunRow.scenario.in_(scenarios),
                ScenarioRunRow.status == "done",
                ScenarioRunRow.finished_at >= start,
                ScenarioRunRow.finished_at < end,
            )
            .order_by(ScenarioRunRow.finished_at, ScenarioRunRow.id)
        )
        async with self._db.sessions() as session:
            rows = await session.execute(query)
            return [(at, scenario) for at, scenario in rows.all() if at is not None]

    async def day_values(
        self, keys: Sequence[str], first: date, until: datetime
    ) -> dict[str, dict[date, float]]:
        """Последнее значение каждого ключа в каждые сутки МСК с `first` до момента `until`."""
        day = cast(func.timezone("Europe/Moscow", MetricRow.ts), Date)
        query = (
            select(MetricRow.key, day, MetricRow.value)
            .where(
                MetricRow.account_id == self._account_id,
                MetricRow.key.in_(keys),
                MetricRow.ts >= day_start(first),
                MetricRow.ts < until,
            )
            .order_by(MetricRow.key, day, MetricRow.ts.desc(), MetricRow.id.desc())
            .ext(distinct_on(MetricRow.key, day))
        )
        out: dict[str, dict[date, float]] = {}
        async with self._db.sessions() as session:
            for key, when, value in (await session.execute(query)).all():
                out.setdefault(key, {})[when] = value
        return out

    async def ledger_entries(self, first: date) -> tuple[list[LedgerEntry], date | None]:
        """Записи журнала прихода с суток `first` и первый день журнала — сутки МСК самой ранней
        записи (`recorded_at`), а не самого раннего эффекта: отчёт о прошлой битве датирован ею, но
        дни до записи журнал не видел (None — журнал пуст)."""
        own = LedgerRow.account_id == self._account_id
        query = (
            select(LedgerRow.day, LedgerRow.kind, LedgerRow.amounts, LedgerRow.items)
            .where(own, LedgerRow.day >= first)
            .order_by(LedgerRow.day, LedgerRow.id)
        )
        async with self._db.sessions() as session:
            rows = (await session.execute(query)).all()
            started = await session.scalar(select(func.min(LedgerRow.recorded_at)).where(own))
        since = tasks_day(started) if started is not None else None
        return [LedgerEntry(d, kind, amounts, items) for d, kind, amounts, items in rows], since

    async def upgrade_progress(self, slot: str, since: datetime) -> UpgradeProgress:
        """Попытки заточки слота `slot` с момента `since` (старт задачи) по эффектам
        `gadget_upgrade` журнала прихода."""
        query = select(LedgerRow.amounts, LedgerRow.items).where(
            LedgerRow.account_id == self._account_id,
            LedgerRow.kind == "gadget_upgrade",
            LedgerRow.at >= since,
            LedgerRow.items.has_key(f"up:{slot}"),
        )
        async with self._db.sessions() as session:
            rows = (await session.execute(query)).all()
        spent = dict.fromkeys(_UPGRADE_KINDS, 0)
        ok = fail = 0
        for amounts, items in rows:
            ok += "ok" in items
            fail += "fail" in items
            for kind in _UPGRADE_KINDS:
                spent[kind] -= amounts.get(f"upgrades_{kind}", 0)
        return UpgradeProgress(len(rows), ok, fail, spent)

    async def metro_runs(self, limit: int, before: int | None) -> list[MetroRunRow]:
        query = (
            select(MetroRunRow)
            .where(MetroRunRow.account_id == self._account_id)
            .order_by(MetroRunRow.id.desc())
            .limit(limit)
        )
        if before is not None:
            query = query.where(MetroRunRow.id < before)
        async with self._db.sessions() as session:
            return list(await session.scalars(query))

    async def metro_run(self, run_id: int) -> MetroRunRow | None:
        async with self._db.sessions() as session:
            row = await session.get(MetroRunRow, run_id)
        return row if row is not None and row.account_id == self._account_id else None

    async def unrecognized(
        self, acked: bool | None, limit: int, before: int | None
    ) -> list[UnrecognizedItem]:
        query = (
            select(UnrecognizedRow, MessageRow.text)
            .join(MessageRow, MessageRow.id == UnrecognizedRow.message_id)
            .where(UnrecognizedRow.account_id == self._account_id)
            .order_by(UnrecognizedRow.id.desc())
            .limit(limit)
        )
        if acked is not None:
            query = query.where(UnrecognizedRow.acked.is_(acked))
        if before is not None:
            query = query.where(UnrecognizedRow.id < before)
        async with self._db.sessions() as session:
            rows = await session.execute(query)
            return [UnrecognizedItem(row, text) for row, text in rows.all()]

    async def ack_unrecognized(self, ids: Sequence[int]) -> int:
        async with self._db.sessions() as session, session.begin():
            done = await session.scalars(
                update(UnrecognizedRow)
                .where(
                    UnrecognizedRow.account_id == self._account_id,
                    UnrecognizedRow.id.in_(ids),
                    UnrecognizedRow.acked.is_(False),
                )
                .values(acked=True)
                .returning(UnrecognizedRow.id)
            )
            return len(done.all())

    async def notifications(
        self, *, unread: bool, level: str | None, limit: int, before: int | None
    ) -> tuple[list[NotificationRow], int, int]:
        """Страница уведомлений от новых к старым, число непрочитанных и из них warn и error."""
        own = NotificationRow.account_id == self._account_id
        query = select(NotificationRow).where(own).order_by(NotificationRow.id.desc()).limit(limit)
        if unread:
            query = query.where(NotificationRow.read.is_(False))
        if level is not None:
            query = query.where(NotificationRow.level == level)
        if before is not None:
            query = query.where(NotificationRow.id < before)
        alert = NotificationRow.level.in_(("warn", "error"))
        counts = (
            select(func.count(), func.count().filter(alert))
            .select_from(NotificationRow)
            .where(own, NotificationRow.read.is_(False))
        )
        async with self._db.sessions() as session:
            rows = list(await session.scalars(query))
            total, alerts = (await session.execute(counts)).one()
        return rows, int(total), int(alerts)

    async def read_notifications(self, up_to_id: int) -> int:
        async with self._db.sessions() as session, session.begin():
            done = await session.scalars(
                update(NotificationRow)
                .where(
                    NotificationRow.account_id == self._account_id,
                    NotificationRow.id <= up_to_id,
                    NotificationRow.read.is_(False),
                )
                .values(read=True)
                .returning(NotificationRow.id)
            )
            return len(done.all())

    async def _settings_row(self) -> SettingsRow | None:
        async with self._db.sessions() as session:
            return await session.scalar(
                select(SettingsRow).where(SettingsRow.account_id == self._account_id)
            )

    async def settings(self) -> tuple[dict[str, Any], int]:
        """Настройки аккаунта в базе (JSON) и их версия; не сохранялись — по умолчанию, версия 0.
        Не проходят проверку текущей сборки — сохранённые поверх умолчаний как есть
        (`stored_values`) и предупреждение в лог: неверное значение видно в форме и исправляется
        прямой записью (раздел 4.4 спеки)."""
        row = await self._settings_row()
        if row is None:
            return Settings().model_dump(mode="json"), 0
        try:
            return Settings.model_validate(row.data).model_dump(mode="json"), row.version
        except ValidationError as exc:
            paths = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
            log.warning(
                "настройки аккаунта %d в базе не проходят проверку: %s", row.account_id, paths
            )
            return stored_values(row.data), row.version

    async def engine(self) -> EngineSection:
        """Секция движка из настроек в базе; не читается — по умолчанию (`engine_section`)."""
        row = await self._settings_row()
        return engine_section(row.data if row is not None else None)

    async def state(self) -> tuple[int, dict[str, Any]]:
        """Последний сохранённый снимок состояния и его версия; до первого — пустой, версия 0."""
        async with self._db.sessions() as session:
            row = await session.scalar(
                select(StateSnapshot).where(StateSnapshot.account_id == self._account_id)
            )
        return (row.version, dict(row.state)) if row else (0, {})

    async def settings_history(
        self, limit: int, before: int | None
    ) -> tuple[list[SettingsVersion], int | None]:
        """До `limit` версий старше `before` от новых к старым, каждая с diff к предыдущей (у
        самой первой — к дефолтам), и курсор следующей страницы (None — страница последняя)."""
        query = (
            select(SettingsHistory)
            .where(SettingsHistory.account_id == self._account_id)
            .order_by(SettingsHistory.version.desc())
            .limit(limit + 1)
        )
        if before is not None:
            query = query.where(SettingsHistory.version < before)
        async with self._db.sessions() as session:
            rows = list(await session.scalars(query))
        # Лишняя строка — и база diff последней версии страницы, и признак следующей страницы.
        data = [_settings_json(r.data) for r in rows]
        bases = [*data[1:], Settings().model_dump(mode="json")]
        page = [
            SettingsVersion(r.version, r.changed_by, r.changed_at, settings_diff(base, new))
            for r, new, base in zip(rows[:limit], data, bases, strict=False)
        ]
        return page, (page[-1].version if len(rows) > limit else None)


def _settings_json(data: dict[str, Any]) -> dict[str, Any]:
    # Версия из прошлой сборки — в текущей форме: секция, появившаяся позже, у неё со значениями
    # по умолчанию, а не отсутствует (иначе diff показал бы её изменённой); значение, которое
    # сборка уже не принимает, — как есть.
    try:
        return Settings.model_validate(data).model_dump(mode="json")
    except ValidationError:
        return stored_values(data)


def _feed_where(
    query: Select[Any],
    model: Any,
    at: Any,
    rank: int,
    flt: FeedFilter,
    after: FeedKey | None,
) -> Select[Any]:
    conds: list[Any] = []
    if flt.since is not None:
        conds.append(at >= flt.since)
    if flt.until is not None:
        conds.append(at < flt.until)
    if flt.chat_id is not None:
        conds.append(model.chat_id == flt.chat_id)
    if flt.status is not None:
        conds.append(model.status == flt.status)
    if flt.source is not None:
        conds.append(model.source == flt.source)
    if after is not None:
        # (at, rank, id) < after при постоянном rank таблицы.
        if rank < after.rank:
            conds.append(at <= after.at)
        elif rank > after.rank:
            conds.append(at < after.at)
        else:
            conds.append(or_(at < after.at, and_(at == after.at, model.id < after.id)))
    return query.where(*conds) if conds else query


def feed_types(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return FEED_TYPES
    types = tuple(t.strip() for t in raw.split(",") if t.strip())
    unknown = [t for t in types if t not in FEED_TYPES]
    if unknown or not types:
        raise ValueError(f"unknown journal types: {unknown}")
    return tuple(t for t in FEED_TYPES if t in types)
