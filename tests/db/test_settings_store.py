import asyncio

import pytest
from sqlalchemy import func, select

from app.db.base import Database
from app.db.models import SettingsHistory
from app.db.settings_store import DbSettingsStore
from app.engine.settings import Settings, SettingsConflict

pytestmark = pytest.mark.db


def _to_live(s: Settings) -> Settings:
    return s.model_copy(update={"engine": s.engine.model_copy(update={"mode": "live"})})


async def test_persist_reload_and_history(clean_db: Database) -> None:
    store = DbSettingsStore(clean_db, account_id=1)
    await store.load()
    assert store.version == 0 and store.current.engine.mode == "dry_run"
    await store.update(_to_live, changed_by="admin")
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
