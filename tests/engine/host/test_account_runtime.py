from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import func, insert, select

from app.config import AppConfig
from app.db.accounts import AccountRepo
from app.db.base import Database
from app.db.models import Account, MessageRow, SettingsRow
from app.engine.facade import LockLostError
from app.engine.fence import Fence, LeaseLost
from app.engine.host.account import AccountRuntime, RuntimeDeps
from app.engine.host.lease import LeaseManager
from app.engine.lag import LoopLagMonitor
from app.logctx import current_account
from tests.conftest import TEST_DB_URL
from tests.engine.helpers import make_msg

pytestmark = pytest.mark.db
ACCOUNT_TASKS = {"pipeline", "gateway", "reconcile", "reactions", "team-forward", "planner"}


class Engines:
    """Движки теста на арендах одного менеджера; в конце теста всё останавливается, аренды
    освобождаются."""

    def __init__(self, db: Database) -> None:
        config = AppConfig(
            _env_file=None, database_url=TEST_DB_URL, transport="fake", planner=False
        )
        self.deps = RuntimeDeps(
            db=db, config=config, accounts=AccountRepo(db), lag=LoopLagMonitor()
        )
        # Продление в тестах не идёт: местный срок аренды с запасом на весь тест.
        self.leases = LeaseManager(db, "test-host", ttl_s=300.0)
        self.runtimes: list[AccountRuntime] = []
        self.crash_loops: list[tuple[int, str]] = []

    async def start(self, account_id: int) -> AccountRuntime:
        fence = await self.leases.acquire(account_id)
        assert isinstance(fence, Fence)
        account = await self.deps.accounts.get(account_id)
        assert account is not None
        runtime = AccountRuntime(account, self.deps, fence, on_crash_loop=self._crash_loop)
        self.runtimes.append(runtime)
        await runtime.start()
        return runtime

    async def _crash_loop(self, account_id: int, task: str) -> None:
        self.crash_loops.append((account_id, task))

    async def close(self) -> None:
        for runtime in self.runtimes:
            await runtime.abort()
            await self.leases.release(runtime.fence)
        await self.leases.close()


@pytest.fixture
async def engines(clean_db: Database) -> AsyncIterator[Engines]:
    made = Engines(clean_db)
    await made.leases.open()
    try:
        yield made
    finally:
        await made.close()


async def _add_account(db: Database) -> int:
    async with db.sessions() as session, session.begin():
        account_id = await session.scalar(
            insert(Account).values(name="Второй").returning(Account.id)
        )
    assert account_id is not None
    return account_id


async def _journaled(db: Database) -> int:
    async with db.sessions() as session:
        return int(await session.scalar(select(func.count()).select_from(MessageRow)) or 0)


async def _settings_row(db: Database, account_id: int = 1) -> tuple[int, Any] | None:
    async with db.sessions() as session:
        row = await session.scalar(select(SettingsRow).where(SettingsRow.account_id == account_id))
        return None if row is None else (row.version, row.data)


async def test_two_runtimes_share_process_without_crosstalk(
    engines: Engines, clean_db: Database
) -> None:
    second = await _add_account(clean_db)
    one = await engines.start(1)
    two = await engines.start(second)
    assert one.facade is not None and two.facade is not None
    assert one.stream is not two.stream
    one_tasks = dict(one.supervisor._tasks)
    two_tasks = dict(two.supervisor._tasks)
    assert set(one_tasks) == set(two_tasks) == ACCOUNT_TASKS
    assert set(one_tasks.values()).isdisjoint(two_tasks.values())
    # Задачи создаются в контексте своего аккаунта: он попадает в каждую строку их логов.
    assert {t.get_context()[current_account] for t in one_tasks.values()} == {1}
    assert {t.get_context()[current_account] for t in two_tasks.values()} == {second}

    await one.facade.pause(by="test")
    assert one.settings.current.engine.paused is True
    assert two.settings.current.engine.paused is False
    assert {"settings", "notification"} <= {e.type for e in one.stream.history()}
    assert two.stream.history() == []

    await one.stop()
    assert all(t.done() for t in one_tasks.values())
    assert not any(t.done() for t in two_tasks.values())
    status = two.facade.status()
    assert status.workers_ok and status.lease_ok and not status.paused


async def test_stop_drains_pipeline_abort_does_not(engines: Engines, clean_db: Database) -> None:
    first = await engines.start(1)
    assert first.pipeline is not None
    for i in range(50):
        await first.pipeline.submit(make_msg(f"m{i}", msg_id=i + 1))
    await first.stop()
    assert await _journaled(clean_db) == 50
    await engines.leases.release(first.fence)

    second = await engines.start(1)
    assert second.pipeline is not None
    for i in range(50):
        await second.pipeline.submit(make_msg(f"n{i}", msg_id=100 + i))
    await second.abort()
    assert second.pipeline.backlog() > 0
    assert await _journaled(clean_db) < 100
    assert not second.supervisor._tasks


async def test_writes_after_lost_lease_refused(engines: Engines, clean_db: Database) -> None:
    runtime = await engines.start(1)
    facade = runtime.facade
    assert facade is not None
    await facade.patch_settings({"engine": {"min_request_interval_s": 2}}, version=0, by="test")
    before = await _settings_row(clean_db)
    assert before is not None and before[0] == 1

    runtime.fence.revoke()
    assert facade.status().lease_ok is False
    with pytest.raises(LeaseLost):
        await facade.patch_settings(
            {"engine": {"min_request_interval_s": 3}}, version=1, by="test"
        )
    with pytest.raises(LockLostError):
        await facade.unkill(by="test")
    assert await _settings_row(clean_db) == before
    assert runtime.settings.current.engine.min_request_interval_s == 2
    await runtime.abort()
