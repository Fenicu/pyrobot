import asyncio
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.db.base import Database

# Соединения, ждущие блокировку, которую держит соединение `:pid` (его транзакция или очередь к
# ней). `pg_blocking_pids` смотрит только на конкретное соединение, а не на все ожидания кластера
# (там могут стоять соединения чужих тестов и самого Postgres).
_BLOCKED_BY = text(
    "SELECT pid FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid)) ORDER BY pid"
)


async def backend_pid(conn: AsyncConnection | AsyncSession) -> int:
    """Номер серверного процесса соединения."""
    return int(await conn.scalar(text("SELECT pg_backend_pid()")) or 0)


async def wait_blocked(db: Database, pid: int, *, timeout_s: float = 5.0) -> list[int]:
    """Ждёт, пока соединение с номером `pid` кого-нибудь заблокирует, и возвращает номера
    ждущих соединений: ожидание — по состоянию Postgres, а не по времени."""
    deadline = time.monotonic() + timeout_s
    while True:
        async with db.sessions() as session:
            waiting = [int(p) for p in await session.scalars(_BLOCKED_BY, {"pid": pid})]
        if waiting:
            return waiting
        if time.monotonic() > deadline:
            raise AssertionError(f"nobody is blocked by backend {pid}")
        await asyncio.sleep(0.01)
