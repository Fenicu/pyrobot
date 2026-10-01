import asyncio
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, update

from app.db.actions import DbActionStore
from app.db.base import Base, Database
from app.db.journal import DbJournal
from app.db.metro import DbMetroRunStore
from app.db.models import Account, NotificationRow, SettingsHistory
from app.db.notifications import DbNotifier
from app.db.planner import DbPlannerStore
from app.db.settings_store import DbSettingsStore
from app.engine.commands import CommandClass
from app.engine.fence import Fence, LeaseLost
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus
from app.engine.planner.types import Wait
from app.engine.settings import Settings
from tests.db.helpers import backend_pid, wait_blocked
from tests.engine.helpers import make_msg

pytestmark = pytest.mark.db
AT = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


async def _set_epoch(db: Database, epoch: int) -> None:
    async with db.sessions() as session, session.begin():
        await session.execute(update(Account).where(Account.id == 1).values(lease_epoch=epoch))


def _fence(epoch: int) -> Fence:
    return Fence(1, epoch, time.monotonic() + 60)


async def _written(db: Database) -> int:
    """Строк во всех таблицах, кроме `accounts`."""
    async with db.sessions() as session:
        return sum(
            [
                await session.scalar(select(func.count()).select_from(table)) or 0
                for table in Base.metadata.sorted_tables
                if table.name != "accounts"
            ]
        )


def _to_live(s: Settings) -> Settings:
    return s.model_copy(update={"engine": s.engine.model_copy(update={"mode": "live"})})


async def _write(db: Database, store: str, fence: Fence) -> None:
    """Одна пишущая транзакция хранилища `store` под оградой `fence`."""
    if store == "journal":
        await DbJournal(db, 1, fence=fence).append(make_msg("Офис"), [], {"x": 1}, 1)
    elif store == "settings":
        settings = DbSettingsStore(db, 1, fence=fence)
        await settings.load()
        await settings.update(_to_live, changed_by="admin")
    elif store == "actions":
        req = ActionRequest(kind=ActionKind.SEND, chat_id=1, text="/job", idempotency_key="k1")
        await DbActionStore(db, 1, fence=fence).create(req, CommandClass.NAV, ActionStatus.INTENT)
    elif store == "planner":
        await DbPlannerStore(db, 1, fence=fence).record(AT, Wait(AT, "busy", ()))
    elif store == "metro":
        record = {"started_at": AT.isoformat(), "duration_s": 60.0, "outcome": "finished"}
        await DbMetroRunStore(db, 1, fence=fence).save(None, "done", record)
    elif store == "notifier":
        await DbNotifier(db, 1, fence=fence).notify("warn", "test_code", "проверка")
    else:
        raise AssertionError(store)


async def test_guard_passes_with_current_epoch(clean_db: Database) -> None:
    await _set_epoch(clean_db, 8)
    fence = _fence(8)
    async with clean_db.sessions() as session, session.begin():
        await fence.guard(session)
    await _write(clean_db, "journal", fence)
    assert fence.alive and await _written(clean_db) == 2  # сообщение и снимок


@pytest.mark.parametrize(
    "store", ["journal", "settings", "actions", "planner", "metro", "notifier"]
)
async def test_write_with_stale_epoch_raises_lease_lost(clean_db: Database, store: str) -> None:
    await _set_epoch(clean_db, 8)
    lost: list[int] = []
    fence = _fence(7)
    fence.on_lost = lambda: lost.append(1)
    with pytest.raises(LeaseLost):
        await _write(clean_db, store, fence)
    assert await _written(clean_db) == 0
    assert not fence.alive and lost == [1]


async def test_settings_lease_lost_is_not_version_conflict(clean_db: Database) -> None:
    await _set_epoch(clean_db, 8)
    await DbSettingsStore(clean_db, 1).update(lambda s: s, changed_by="admin")
    store = DbSettingsStore(clean_db, 1, fence=_fence(7))
    await store.load()
    with pytest.raises(LeaseLost):
        await store.update(_to_live, changed_by="admin", expected_version=1)
    assert store.version == 1 and store.current.engine.mode == "dry_run"
    async with clean_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SettingsHistory)) == 1


async def _acquire(db: Database) -> int:
    """Захват аренды другим хостом на своём соединении."""
    async with db.engine.connect() as conn, conn.begin():
        epoch = await conn.scalar(
            update(Account)
            .where(Account.id == 1)
            .values(lease_epoch=Account.lease_epoch + 1)
            .returning(Account.lease_epoch)
        )
    return int(epoch)


async def test_acquire_waits_for_fenced_write(clean_db: Database) -> None:
    await _set_epoch(clean_db, 7)
    fence = _fence(7)
    async with clean_db.sessions() as session, session.begin():
        await fence.guard(session)
        session.add(NotificationRow(account_id=1, level="info", code="fenced", text="A"))
        acquire = asyncio.create_task(_acquire(clean_db))
        # Захват дошёл до строки аккаунта и ждёт блокировку ограждённой транзакции.
        await wait_blocked(clean_db, await backend_pid(session))
        assert not acquire.done()
    # Захват ждал коммита ограждённой транзакции и прошёл после него.
    assert await asyncio.wait_for(acquire, 5) == 8
    assert await _written(clean_db) == 1
    with pytest.raises(LeaseLost):
        await _write(clean_db, "journal", fence)
    assert await _written(clean_db) == 1
