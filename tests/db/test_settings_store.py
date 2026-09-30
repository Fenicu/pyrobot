import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text, update

from app.db.base import Database
from app.db.models import Account, SettingsHistory, SettingsRow
from app.db.settings_store import DbSettingsStore, LeaseHeld, direct_update
from app.engine.fence import Fence
from app.engine.host.lease import LeaseManager
from app.engine.settings import Settings, SettingsConflict

pytestmark = pytest.mark.db


def _to_live(s: Settings) -> Settings:
    return s.model_copy(update={"engine": s.engine.model_copy(update={"mode": "live"})})


async def test_persist_reload_and_history(clean_db: Database) -> None:
    store = DbSettingsStore(clean_db, account_id=1)
    await store.load()
    assert store.version == 0 and store.current.engine.mode == "dry_run"
    saved, version = await store.update(_to_live, changed_by="admin")
    assert (saved.engine.mode, version) == ("live", 1)
    again = DbSettingsStore(clean_db, account_id=1)
    await again.load()
    assert again.version == 1 and again.current.engine.mode == "live"
    async with clean_db.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(SettingsHistory)) == 1


async def test_conflict_between_two_stores(clean_db: Database) -> None:
    a = DbSettingsStore(clean_db, 1)
    b = DbSettingsStore(clean_db, 1)
    await a.load()
    await b.load()
    await a.update(_to_live, changed_by="a")
    with pytest.raises(SettingsConflict):
        await b.update(_to_live, changed_by="b")


async def test_load_waits_for_update_lock(clean_db: Database) -> None:
    store = DbSettingsStore(clean_db, 1)
    async with store._lock:
        task = asyncio.create_task(store.load())
        await asyncio.sleep(0.05)
        assert not task.done()
    await task


async def test_listeners_after_save(clean_db: Database) -> None:
    store = DbSettingsStore(clean_db, 1)
    await store.load()
    seen: list[tuple[str, int]] = []
    store.listeners.append(lambda s, v: seen.append((s.engine.mode, v)))
    await store.update(_to_live, changed_by="admin")
    assert seen == [("live", 1)]
    with pytest.raises(SettingsConflict):
        await store.update(_to_live, changed_by="admin", expected_version=0)
    assert seen == [("live", 1)]


async def _waiting_locks(db: Database) -> int:
    async with db.sessions() as session:
        return int(
            await session.scalar(text("SELECT count(*) FROM pg_locks WHERE NOT granted")) or 0
        )


async def _until_waiting(db: Database, count: int) -> None:
    for _ in range(250):  # до 5 с
        if await _waiting_locks(db) >= count:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"{count} lock waits expected")


async def test_acquire_waits_for_direct_update(clean_db: Database) -> None:
    await direct_update(clean_db, 1, lambda s: s, changed_by="admin", expected_version=0)
    leases = LeaseManager(clean_db, "host-a", lock_timeout_s=10.0)
    await leases.open()
    done: list[str] = []
    try:
        # Прямая запись держит строку аккаунта FOR SHARE и ждёт строку настроек, которую держит
        # другое соединение: так она стоит посреди своей транзакции.
        async with clean_db.engine.connect() as blocker, blocker.begin():
            await blocker.execute(text("SELECT 1 FROM settings WHERE account_id = 1 FOR UPDATE"))
            write = asyncio.create_task(
                direct_update(clean_db, 1, _to_live, changed_by="admin", expected_version=1)
            )
            write.add_done_callback(lambda _: done.append("write"))
            await _until_waiting(clean_db, 1)
            acquire = asyncio.create_task(leases.acquire(1))
            acquire.add_done_callback(lambda _: done.append("acquire"))
            # Захват (UPDATE accounts) ждёт FOR SHARE прямой записи.
            await _until_waiting(clean_db, 2)
            assert done == []
        fence = await asyncio.wait_for(acquire, 5)
        assert isinstance(fence, Fence)
        assert await write == (_to_live(Settings()), 2)
        assert done == ["write", "acquire"]
        # Движок читает настройки после захвата и видит запись, начатую до него.
        store = DbSettingsStore(clean_db, 1, fence=fence)
        await store.load()
        assert (store.version, store.current.engine.mode) == (2, "live")
        await leases.release(fence)
    finally:
        await leases.close()


async def _lease(db: Database, holder: str | None, expires_in: timedelta | None) -> None:
    expires = func.now() + expires_in if expires_in is not None else None
    async with db.sessions() as session, session.begin():
        await session.execute(
            update(Account)
            .where(Account.id == 1)
            .values(lease_holder=holder, lease_expires_at=expires)
        )


async def test_direct_update_refused_with_active_lease(clean_db: Database) -> None:
    await _lease(clean_db, "host-a", timedelta(minutes=1))
    with pytest.raises(LeaseHeld):
        await direct_update(clean_db, 1, _to_live, changed_by="admin", expected_version=None)
    async with clean_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SettingsRow)) == 0
        assert await session.scalar(select(func.count()).select_from(SettingsHistory)) == 0
    # Истёкшая аренда и освобождённая — прямой записи не мешают.
    await _lease(clean_db, "host-a", timedelta(seconds=-1))
    saved = await direct_update(clean_db, 1, _to_live, changed_by="admin", expected_version=0)
    assert saved == (_to_live(Settings()), 1)
    await _lease(clean_db, None, None)
    with pytest.raises(SettingsConflict):
        await direct_update(clean_db, 1, _to_live, changed_by="admin", expected_version=0)
    await direct_update(clean_db, 1, lambda s: s, changed_by="b", expected_version=1)
    async with clean_db.sessions() as session:
        history = (
            await session.execute(
                select(SettingsHistory.version, SettingsHistory.changed_by).order_by(
                    SettingsHistory.id
                )
            )
        ).all()
    assert [tuple(h) for h in history] == [(1, "admin"), (2, "b")]
