import asyncio
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import AppConfig
from app.db.accounts import AccountInfo, AccountRepo
from app.db.base import Database
from app.engine.fence import Fence
from app.engine.host.account import AccountRuntime, RuntimeDeps
from app.engine.host.codes import CodeLimiter
from app.engine.lag import LoopLagMonitor
from app.engine.tg_auth import TgState
from app.engine.transport.fake import FakeTgBackend
from app.main import Runtime
from tests.engine.helpers import tg_auth, until


@pytest.fixture
async def runtime() -> AsyncIterator[AccountRuntime]:
    """Движок аккаунта без старта: база не открывается, пока к ней не обратятся."""
    config = AppConfig(_env_file=None, transport="fake")
    db = Database(config.database_url)
    deps = RuntimeDeps(
        db=db,
        config=config,
        accounts=AccountRepo(db),
        lag=LoopLagMonitor(),
        codes=CodeLimiter(10),
    )
    account = AccountInfo(
        id=1,
        owner_id=None,
        name="Основной",
        status="enabled",
        status_reason=None,
        tg_user_id=None,
        engine_generation=0,
    )

    async def crash_loop(account_id: int, task: str) -> None:
        pass

    fence = Fence(1, 1, time.monotonic() + 60.0)
    try:
        yield AccountRuntime(account, deps, fence, on_crash_loop=crash_loop)
    finally:
        await db.dispose()


class _Probe:
    def __init__(self) -> None:
        self.calls = 0

    async def probe(self) -> None:
        self.calls += 1


async def test_tg_probe_runs_only_while_online(runtime: AccountRuntime) -> None:
    probe = _Probe()
    runtime._kurigram = probe  # type: ignore[assignment]
    runtime.tg = tg_auth(FakeTgBackend(authorized=True))
    runtime.tg_probe_s = 0.01
    task: asyncio.Task[Any] = asyncio.create_task(runtime._probe_tg())
    try:
        await asyncio.sleep(0.05)
        assert probe.calls == 0
        assert (await runtime.tg.boot()).state is TgState.ONLINE
        await until(lambda: probe.calls >= 2)
        await runtime.tg.mark_lost()
        await asyncio.sleep(0.02)
        seen = probe.calls
        await asyncio.sleep(0.05)
        assert probe.calls == seen
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class _Auth:
    def __init__(self) -> None:
        self.purges = 0

    async def purge_expired(self) -> int:
        self.purges += 1
        if self.purges == 1:
            raise ConnectionError("db down")
        return 0


async def test_session_purge_runs_periodically_and_survives_errors() -> None:
    runtime = Runtime(AppConfig(_env_file=None, transport="fake"))
    auth = _Auth()
    runtime.auth = auth  # type: ignore[assignment]
    runtime.session_purge_s = 0.01
    task: asyncio.Task[Any] = asyncio.create_task(runtime._purge_sessions())
    try:
        await until(lambda: auth.purges >= 3)
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.db.dispose()


async def test_pool_size_from_config() -> None:
    config = AppConfig(_env_file=None, transport="fake", db_pool_size=3, db_max_overflow=4)
    runtime = Runtime(config)
    try:
        pool: Any = runtime.db.engine.pool
        assert (pool.size(), pool._max_overflow) == (3, 4)
    finally:
        await runtime.db.dispose()


async def test_database_pool_defaults_match_config() -> None:
    db = Database(AppConfig.model_fields["database_url"].default)
    try:
        pool: Any = db.engine.pool
        assert (pool.size(), pool._max_overflow) == (
            AppConfig.model_fields["db_pool_size"].default,
            AppConfig.model_fields["db_max_overflow"].default,
        )
    finally:
        await db.dispose()


async def test_stop_cancels_planner_before_closing_gateway(runtime: AccountRuntime) -> None:
    events: list[str] = []

    async def planner() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append("planner")
            raise

    class _Gateway:
        async def shutdown(self) -> None:
            events.append("gateway")

    runtime.gateway = _Gateway()  # type: ignore[assignment]
    runtime.supervisor.start("planner", planner)
    await asyncio.sleep(0)
    await runtime.stop()
    assert events == ["planner", "gateway"]


async def test_planner_not_ready_while_pipeline_unhealthy(runtime: AccountRuntime) -> None:
    runtime.pipeline = SimpleNamespace(healthy=False)  # type: ignore[assignment]
    assert runtime._planner_ready() == "pipeline_unhealthy"


async def test_planner_not_ready_reason_offline_before_spending_block(
    runtime: AccountRuntime,
) -> None:
    # «spending_blocked» цикл понимает как «можно продолжить забег метро»: отдаётся последней.
    runtime.pipeline = SimpleNamespace(healthy=True)  # type: ignore[assignment]
    runtime.gateway = SimpleNamespace(  # type: ignore[assignment]
        kill_reason=None, spending_blocked="reconcile_required"
    )
    cannot = runtime._can_send()
    assert cannot is not None
    assert runtime._planner_ready() == cannot


async def test_planner_held_while_walk_offer_handled(runtime: AccountRuntime) -> None:
    # Встреча с биржевиком на прогулке в работе: очередная /walk попала бы в экран после клика.
    runtime.pipeline = SimpleNamespace(healthy=True)  # type: ignore[assignment]
    runtime.gateway = SimpleNamespace(  # type: ignore[assignment]
        kill_reason=None, spending_blocked=None
    )
    runtime._can_send = lambda: None  # type: ignore[method-assign]
    runtime.bulls_walk = SimpleNamespace(holding=True)  # type: ignore[assignment]
    assert runtime._planner_ready() == "bulls_walk"
    runtime.bulls_walk = SimpleNamespace(holding=False)  # type: ignore[assignment]
    assert runtime._planner_ready() is None


async def test_memwatch_shared_with_container_and_http() -> None:
    from app.main import create_application
    from app.memwatch import InFlightMiddleware

    app = create_application(AppConfig(_env_file=None, transport="fake"))
    runtime = app.state.runtime
    try:
        assert runtime.container.memwatch is runtime.memwatch
        assert [
            m.kwargs["inflight"] for m in app.user_middleware if m.cls is InFlightMiddleware
        ] == [runtime.memwatch.inflight]
    finally:
        await runtime.db.dispose()
