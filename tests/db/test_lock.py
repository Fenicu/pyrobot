import asyncio

import pytest

from app.db.base import Database
from app.db.lock import SingleInstanceLock
from tests.conftest import TEST_DB_URL

pytestmark = pytest.mark.db


async def test_second_instance_blocked(db: Database) -> None:
    other = Database(TEST_DB_URL)
    first, second = SingleInstanceLock(db), SingleInstanceLock(other)
    try:
        assert await first.acquire() and first.held
        assert not await second.acquire()
        await first.release()
        assert await second.acquire()
    finally:
        await first.release()
        await second.release()
        await other.dispose()


async def test_lost_connection_detected(db: Database) -> None:
    lock = SingleInstanceLock(db)
    assert await lock.acquire()
    assert await lock.check()
    assert lock._conn is not None
    await lock._conn.invalidate()
    assert not await lock.check()
    assert not lock.held
    await lock.release()


async def test_check_timeout_is_failure() -> None:
    class HangingConn:
        async def scalar(self, *args: object, **kwargs: object) -> bool:
            await asyncio.Event().wait()
            return True

    db = Database(TEST_DB_URL)
    try:
        lock = SingleInstanceLock(db, check_timeout_s=0.05)
        lock._conn = HangingConn()  # type: ignore[assignment]
        lock.held = True
        assert await asyncio.wait_for(lock.check(), 1) is False
        assert not lock.held
    finally:
        await db.dispose()
