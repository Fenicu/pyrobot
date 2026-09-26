from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.base import Database

LOCK_KEY = 0x7079726F626F74


class SingleInstanceLock:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._conn: AsyncConnection | None = None
        self.held = False

    async def acquire(self) -> bool:
        conn = await self._db.engine.connect()
        got = await conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})
        if got:
            self._conn = conn
            self.held = True
            return True
        await conn.close()
        return False

    async def check(self) -> bool:
        if self._conn is None:
            self.held = False
            return False
        try:
            owned = await self._conn.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                    "AND pid = pg_backend_pid() AND granted)"
                )
            )
        except Exception:
            self.held = False
            return False
        self.held = self.held and bool(owned)
        return self.held

    async def release(self) -> None:
        conn, self._conn = self._conn, None
        self.held = False
        if conn is None:
            return
        try:
            await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        except Exception:
            pass
        finally:
            await conn.close()
