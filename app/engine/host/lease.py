import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import CursorResult, TextClause, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.base import Database
from app.engine.fence import Fence, LeaseLost

log = logging.getLogger(__name__)

# Первый ключ двухключевых advisory-блокировок аккаунтов ("pyro"), второй — id аккаунта.
LOCK_KEY = 0x7079726F
# SQLSTATE lock_not_available: запрос упёрся в lock_timeout.
_LOCK_NOT_AVAILABLE = "55P03"

_LOCK_TIMEOUT = text("SELECT set_config('lock_timeout', :value, false)")
_LOCK = text("SELECT pg_try_advisory_lock(:key, :id)")
_UNLOCK = text("SELECT pg_advisory_unlock(:key, :id)")
# Свою аренду (тот же держатель) хост захватывает сразу, чужую — только после её срока.
_ACQUIRE = text(
    "UPDATE accounts SET lease_holder = :holder, lease_epoch = lease_epoch + 1, "
    "lease_expires_at = now() + make_interval(secs => :ttl) "
    "WHERE id = :id AND (lease_holder IS NULL OR lease_expires_at < now() "
    "OR lease_holder = :holder) "
    "RETURNING lease_epoch"
)
_LEFT = text(
    "SELECT CAST(EXTRACT(EPOCH FROM lease_expires_at - now()) AS float8) FROM accounts "
    "WHERE id = :id"
)
# Все аренды хоста одним запросом, по парам (аккаунт, эпоха).
_RENEW = text(
    "UPDATE accounts SET lease_expires_at = now() + make_interval(secs => :ttl) "
    "WHERE lease_holder = :holder AND (id, lease_epoch) IN "
    "(SELECT * FROM unnest(CAST(:ids AS integer[]), CAST(:epochs AS bigint[]))) "
    "RETURNING id, lease_epoch"
)
# Держатель очищается: продление, собранное до освобождения, аренду уже не находит.
_RELEASE = text(
    "UPDATE accounts SET lease_holder = NULL, lease_expires_at = NULL "
    "WHERE id = :id AND lease_holder = :holder AND lease_epoch = :epoch"
)


@dataclass(frozen=True)
class Busy:
    """Аккаунт не захвачен: блокировка у другого хоста или действует чужая аренда; повторить
    через `retry_in_s`."""

    reason: Literal["locked_elsewhere", "lease_active"]
    retry_in_s: float


class LeaseManager:
    """Аренды аккаунтов хоста (раздел 4.2 спеки). Advisory-блокировка аккаунта только
    упорядочивает захват, действует аренда с TTL в `accounts`. Все запросы идут по выделенному
    соединению блокировок, каждый — отдельной транзакцией (AUTOCOMMIT), ожидания — вне
    транзакций. Аренды принадлежат экземпляру соединения: после обрыва блокировки забываются,
    прежние эпохи не продлеваются, и их ограды истекают сами."""

    def __init__(
        self,
        db: Database,
        holder: str,
        *,
        ttl_s: float = 15.0,
        renew_every_s: float = 5.0,
        margin_s: float = 3.0,
        lock_timeout_s: float = 2.0,
        retry_s: float = 1.0,
        busy_retry_s: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.holder = holder
        self._db = db
        self._ttl = ttl_s
        self._renew_every = renew_every_s
        self._margin = margin_s
        self._lock_timeout = lock_timeout_s
        self._retry = retry_s
        self._busy_retry = busy_retry_s
        self._monotonic = monotonic
        self._conn: AsyncConnection | None = None
        # Запросы по соединению блокировок идут по одному.
        self._io = asyncio.Lock()
        self._closed = False
        # Блокировки, взятые на текущем соединении. Повторный захват в Postgres входим и
        # требует своего снятия, поэтому взятая блокировка не берётся снова.
        self._locks: set[int] = set()
        # Текущая ограда каждого аккаунта. Продлеваются живые, чья блокировка — на текущем
        # соединении; за сроками остальных следит run().
        self._fences: dict[int, Fence] = {}
        # Будит слежение за сроками: срок новой ограды может наступить раньше, чем оно проснулось
        # бы само, — t снят до захвата, а захват мог ждать строку до lock_timeout.
        self._fence_added = asyncio.Event()
        self.on_connection_lost: Callable[[], Awaitable[None]] | None = None

    def healthy(self) -> bool:
        """Соединение блокировок открыто; обрыв замечается на следующем запросе."""
        return self._conn is not None

    def held(self) -> list[int]:
        """Аккаунты, блокировки которых хост держит на текущем соединении."""
        return sorted(self._locks)

    async def open(self) -> None:
        self._closed = False
        await self._connect()

    async def close(self) -> None:
        """Закрыть соединение блокировок (вместе с ним снимаются все блокировки хоста). Аренды
        не освобождаются — это делает `release()` после остановки движков."""
        self._closed = True
        async with self._io:
            conn, self._conn = self._conn, None
            self._locks.clear()
            if conn is not None:
                await _discard(conn)

    async def acquire(self, account_id: int) -> Fence | Busy:
        """Блокировка аккаунта (если её ещё нет у хоста), затем условный захват аренды. Без
        открытого соединения блокировок — `ConnectionError`."""
        async with self._io:
            conn = self._conn
            if conn is None:
                raise ConnectionError("lease lock connection is not open")
            if account_id not in self._locks:
                if not await self._scalar(conn, _LOCK, {"key": LOCK_KEY, "id": account_id}):
                    return Busy("locked_elsewhere", self._busy_retry)
                self._locks.add(account_id)
            # Местное время до запроса: срок ограды наступит раньше lease_expires_at в базе.
            t = self._monotonic()
            try:
                epoch = await self._scalar(
                    conn, _ACQUIRE, {"holder": self.holder, "ttl": self._ttl, "id": account_id}
                )
            except DBAPIError as exc:
                if _sqlstate(exc) != _LOCK_NOT_AVAILABLE:
                    raise
                # Строку аккаунта держит ограждённая транзакция действующей аренды.
                return Busy("lease_active", self._retry)
            if epoch is None:
                # Аренда прежнего держателя ещё действует; блокировка остаётся за хостом.
                left = await self._scalar(conn, _LEFT, {"id": account_id})
                return Busy("lease_active", max(left or 0.0, 0.0) + 1.0)
            fence = Fence(
                account_id, int(epoch), t + self._ttl - self._margin, monotonic=self._monotonic
            )
            previous = self._fences.get(account_id)
            self._fences[account_id] = fence
            self._fence_added.set()
        if previous is not None:
            # Эпоха в базе уже новая: прежняя ограда аккаунта аренду потеряла.
            previous.revoke()
        return fence

    async def release(self, fence: Fence) -> None:
        """Штатное освобождение после остановки движка. Ограда уходит из набора продления; до
        местного срока аренда освобождается с очисткой держателя, после — в базу ничего не
        пишется (следующий держатель ждёт `lease_expires_at`); затем снимается блокировка.
        Ограда, которая уже не текущая у аккаунта, ничего не трогает. Ошибки базы не выходят
        наружу: неосвобождённая аренда истекает сама."""
        account_id = fence.account_id
        if self._fences.get(account_id) is not fence:
            return
        del self._fences[account_id]
        async with self._io:
            conn = self._conn
            # Блокировка была на прежнем соединении: аренда той эпохи истекает сама.
            if conn is None or account_id not in self._locks:
                return
            if fence.alive:
                params = {"id": account_id, "holder": self.holder, "epoch": fence.epoch}
                try:
                    await self._execute(conn, _RELEASE, params)
                except Exception as exc:
                    log.warning("lease of account %s not released: %s", account_id, _reason(exc))
                    if self._conn is not conn:
                        return
            try:
                await self._execute(conn, _UNLOCK, {"key": LOCK_KEY, "id": account_id})
            except Exception as exc:
                log.warning("lock of account %s not released: %s", account_id, _reason(exc))
                return
            self._locks.discard(account_id)

    async def renew_once(self) -> None:
        """Одно продление живых аренд текущего соединения. Ответ разбирается по парам
        (аккаунт, эпоха): пара, чей аккаунт уже в другой эпохе или освобождён, игнорируется;
        вернулась — ограда продлевается, не вернулась — отзывается. Ошибка запроса (в том числе
        lock_timeout) выходит наружу, повтор — в `run()`."""
        conn = self._conn
        pairs = [
            (fence.account_id, fence.epoch)
            for fence in self._fences.values()
            if fence.alive and fence.account_id in self._locks
        ]
        if conn is None or not pairs:
            return
        t, renewed = await self._renew(conn, pairs)
        deadline = t + self._ttl - self._margin
        for account_id, epoch in pairs:
            fence = self._fences.get(account_id)
            if fence is None or fence.epoch != epoch:
                continue
            if (account_id, epoch) in renewed:
                fence.extend(epoch, deadline)
            else:
                log.warning("lease of account %s (epoch %s) lost on renewal", account_id, epoch)
                fence.revoke()

    async def run(self) -> None:
        """Продление каждые `renew_every_s`, после ошибки — через `retry_s`; оборванное
        соединение блокировок открывается заново. Рядом идёт слежение за местными сроками
        оград: ограда без вызовов сама о сроке не узнаёт, а аварийная остановка — по сроку, в
        том числе пока продление висит или соединения нет."""
        watcher = asyncio.create_task(self._watch_deadlines(), name="lease-deadlines")
        try:
            while not self._closed:
                started = self._monotonic()
                try:
                    if self._conn is None:
                        await self._connect()
                    await self.renew_once()
                except Exception as exc:
                    log.warning(
                        "lease renewal failed, retry in %.1fs: %s", self._retry, _reason(exc)
                    )
                    await asyncio.sleep(self._retry)
                else:
                    await asyncio.sleep(max(started + self._renew_every - self._monotonic(), 0.0))
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher

    async def _watch_deadlines(self) -> None:
        """Просыпается к ближайшему местному сроку живых оград и при каждом новом захвате;
        истёкшей ограде — `check()`, чтобы `on_lost` сработал по сроку. Продление сроки только
        отодвигает, поэтому раннее пробуждение лишь пересчитывает сон."""
        while True:
            self._fence_added.clear()
            now = self._monotonic()
            wake: float | None = None
            for fence in list(self._fences.values()):
                if fence.alive:
                    wake = fence.deadline if wake is None else min(wake, fence.deadline)
                else:
                    with contextlib.suppress(LeaseLost):
                        fence.check()
            timeout = None if wake is None else max(wake - now, 0.0)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._fence_added.wait(), timeout)

    async def _renew(
        self, conn: AsyncConnection, pairs: list[tuple[int, int]]
    ) -> tuple[float, set[tuple[int, int]]]:
        """Запрос продления: местное время до запроса и вернувшиеся пары."""
        async with self._io:
            if self._conn is not conn:
                raise ConnectionError("lease lock connection changed before renewal")
            params = {
                "holder": self.holder,
                "ttl": self._ttl,
                "ids": [account_id for account_id, _ in pairs],
                "epochs": [epoch for _, epoch in pairs],
            }
            t = self._monotonic()
            result = await self._execute(conn, _RENEW, params)
            return t, {(int(account_id), int(epoch)) for account_id, epoch in result}

    async def _connect(self) -> None:
        async with self._io:
            if self._closed or self._conn is not None:
                return
            conn = await self._db.engine.connect()
            try:
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                value = f"{round(self._lock_timeout * 1000)}ms"
                await conn.execute(_LOCK_TIMEOUT, {"value": value})
            except (Exception, asyncio.CancelledError):
                await _discard(conn)
                raise
            self._conn = conn

    async def _scalar(
        self, conn: AsyncConnection, stmt: TextClause, params: Mapping[str, Any]
    ) -> Any:
        return (await self._execute(conn, stmt, params)).scalar()

    async def _execute(
        self, conn: AsyncConnection, stmt: TextClause, params: Mapping[str, Any]
    ) -> CursorResult[*tuple[Any, ...]]:
        """Запрос по соединению блокировок (под `_io`). Ошибка, о которой сообщил сервер,
        соединение не трогает; обрыв, отмена или другой сбой — соединение потеряно: неизвестно,
        чем кончился запрос (взята ли блокировка)."""
        try:
            return await conn.execute(stmt, params)
        except DBAPIError as exc:
            if exc.connection_invalidated or _sqlstate(exc) is None:
                await self._lost(conn)
            raise
        except (Exception, asyncio.CancelledError):
            await self._lost(conn)
            raise

    async def _lost(self, conn: AsyncConnection) -> None:
        was_active = self._conn is conn
        if was_active:
            self._conn = None
            self._locks.clear()
            log.warning("lease lock connection lost: locks forgotten, leases expire by TTL")
        await _discard(conn)
        if was_active and self.on_connection_lost is not None:
            try:
                await self.on_connection_lost()
            except Exception:
                log.exception("lease connection lost callback failed")


async def _discard(conn: AsyncConnection) -> None:
    """Соединение блокировок не возвращается в пул: закрывается вместе со своими блокировками и
    lock_timeout."""
    try:
        await conn.invalidate()
    except Exception:
        log.debug("lease lock connection not invalidated", exc_info=True)
    try:
        await conn.close()
    except Exception:
        log.debug("lease lock connection not closed", exc_info=True)


def _sqlstate(exc: DBAPIError) -> str | None:
    state = getattr(exc.orig, "sqlstate", None)
    return state if isinstance(state, str) else None


def _reason(exc: BaseException) -> str:
    return str(exc.orig) if isinstance(exc, DBAPIError) else repr(exc)
