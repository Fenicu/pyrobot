import asyncio
from types import SimpleNamespace
from typing import Any

from app.config import AppConfig
from app.engine.tg_auth import TgAuthManager, TgState
from app.engine.transport.fake import FakeTgBackend
from app.main import Runtime
from tests.engine.helpers import until


class _Probe:
    def __init__(self) -> None:
        self.calls = 0

    async def probe(self) -> None:
        self.calls += 1


async def test_tg_probe_runs_only_while_online() -> None:
    runtime = Runtime(AppConfig(_env_file=None, transport="fake"))
    probe = _Probe()
    runtime._kurigram = probe  # type: ignore[assignment]
    runtime.tg = TgAuthManager(FakeTgBackend(authorized=True), expected_user_id=267519921)
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
        await runtime.db.dispose()


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


async def test_stop_cancels_planner_before_closing_gateway() -> None:
    runtime = Runtime(AppConfig(_env_file=None, transport="fake"))
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


async def test_planner_not_ready_while_pipeline_unhealthy() -> None:
    runtime = Runtime(AppConfig(_env_file=None, transport="fake"))
    runtime.pipeline = SimpleNamespace(healthy=False)  # type: ignore[assignment]
    try:
        assert runtime._planner_ready() == "pipeline_unhealthy"
    finally:
        await runtime.db.dispose()
