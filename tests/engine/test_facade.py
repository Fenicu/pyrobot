import asyncio

import pytest

from app.engine.bus import Bus
from app.engine.clock import SystemClock
from app.engine.facade import EngineFacade
from app.engine.gateway.gateway import ActionGateway
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus
from app.engine.lag import LoopLagMonitor
from app.engine.memory import MemoryActionStore, MemoryJournal
from app.engine.parsing import default_parser
from app.engine.pipeline import NullReducer, Pipeline
from app.engine.settings import SettingsChange, StaticSettings
from app.engine.tg_auth import TgAuthManager, TgState
from app.engine.transport.fake import FakeTgBackend, FakeTransport
from tests.engine.helpers import until


def build(authorized: bool = True, settings: StaticSettings | None = None) -> EngineFacade:
    settings = settings or StaticSettings()
    pipeline = Pipeline(
        journal=MemoryJournal(), parser=default_parser(), reducer=NullReducer(), bus=Bus()
    )
    gateway = ActionGateway(
        transport=FakeTransport(),
        store=MemoryActionStore(),
        settings=settings,
        latest=pipeline.latest,
        boundary=lambda: pipeline.last_journal_id,
        clock=SystemClock(),
    )
    tg = TgAuthManager(FakeTgBackend(authorized=authorized), expected_user_id=267519921)
    return EngineFacade(
        settings=settings, gateway=gateway, pipeline=pipeline, tg_auth=tg, lag=LoopLagMonitor()
    )


async def test_status_and_ready() -> None:
    f = build()
    assert not f.ready()
    await f.tg.boot()
    st = f.status()
    assert st.mode == "dry_run" and st.tg.state is TgState.ONLINE and f.ready()
    f.gateway.block_spending("reconcile_required")
    assert not f.ready() and f.status().spending_blocked == "reconcile_required"
    await f.reconciled(by="admin")
    assert f.ready()


async def test_kill_latches_even_if_persist_fails() -> None:
    class Failing(StaticSettings):
        async def update(self, change: SettingsChange, **kw: object) -> object:  # type: ignore[override]
            raise ConnectionError("db down")

    f = build(settings=Failing())
    await f.tg.boot()
    pending = asyncio.create_task(
        f.gateway.submit(ActionRequest(kind=ActionKind.SEND, chat_id=1, text="😎Я"))
    )
    await until(lambda: f.gateway.queue_size == 1)
    await f.kill("test", by="admin")
    assert (await pending).status is ActionStatus.SUPPRESSED
    assert f.status().killed and f.status().kill_reason == "test" and not f.ready()
    with pytest.raises(ConnectionError):
        await f.unkill(by="admin")
    assert f.status().killed


async def test_kill_empty_reason_still_latches() -> None:
    class Failing(StaticSettings):
        async def update(self, change: SettingsChange, **kw: object) -> object:  # type: ignore[override]
            raise ConnectionError("db down")

    f = build(settings=Failing())
    await f.tg.boot()
    await f.kill("", by="admin")
    assert f.status().killed is True
    assert f.ready() is False


async def test_unkill_restores() -> None:
    f = build()
    await f.tg.boot()
    await f.kill("test", by="admin")
    await f.unkill(by="admin")
    assert not f.status().killed and f.ready()
