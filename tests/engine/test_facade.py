import asyncio
from collections.abc import Callable

import pytest

from app.engine.bus import Bus
from app.engine.clock import SystemClock
from app.engine.facade import EngineFacade, LockLostError
from app.engine.gateway.gateway import ActionGateway
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus
from app.engine.lag import LoopLagMonitor
from app.engine.memory import MemoryActionStore, MemoryJournal
from app.engine.notify import NotifierPort
from app.engine.parsing import default_parser
from app.engine.pipeline import NullReducer, Pipeline
from app.engine.settings import SettingsChange, StaticSettings
from app.engine.tg_auth import TgAuthBackend, TgAuthManager, TgState
from app.engine.transport.fake import FakeTgBackend, FakeTransport
from tests.engine.helpers import GAME, until


def build(
    authorized: bool = True,
    settings: StaticSettings | None = None,
    lock_ok: Callable[[], bool] = lambda: True,
    backend: TgAuthBackend | None = None,
    notifier: NotifierPort | None = None,
) -> EngineFacade:
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
    tg = TgAuthManager(backend or FakeTgBackend(authorized=authorized), expected_user_id=267519921)
    return EngineFacade(
        settings=settings,
        gateway=gateway,
        pipeline=pipeline,
        tg_auth=tg,
        lag=LoopLagMonitor(),
        lock_ok=lock_ok,
        notifier=notifier,
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
        f.gateway.submit(ActionRequest(kind=ActionKind.SEND, chat_id=GAME, text="😎Я"))
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


async def test_unkill_refused_after_lock_lost() -> None:
    held = [True]
    f = build(lock_ok=lambda: held[0])
    await f.tg.boot()
    await f.gateway.kill("lock_lost")
    held[0] = False
    with pytest.raises(LockLostError):
        await f.unkill(by="admin")
    assert f.gateway.kill_reason == "lock_lost"
    assert f.settings.current.engine.killed is False


class _Recorder:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    async def notify(self, level: str, code: str, text: str) -> None:
        self.items.append((level, code, text))


async def test_audit_notifications_name_actor() -> None:
    rec = _Recorder()
    held = [True]
    f = build(lock_ok=lambda: held[0], notifier=rec)
    await f.kill("maintenance", by="alice")
    await f.unkill(by="bob")
    await f.reconciled(by="carol")
    assert [(lvl, code) for lvl, code, _ in rec.items] == [
        ("info", "engine_killed"),
        ("info", "engine_unkilled"),
        ("info", "engine_reconciled"),
    ]
    assert "alice" in rec.items[0][2] and "maintenance" in rec.items[0][2]
    assert "bob" in rec.items[1][2] and "carol" in rec.items[2][2]
    held[0] = False
    with pytest.raises(LockLostError):
        await f.unkill(by="bob")
    assert len(rec.items) == 3


async def test_pause_resume_persist_and_audit() -> None:
    rec = _Recorder()
    f = build(notifier=rec)
    await f.pause(by="alice")
    st = f.status()
    assert st.paused and st.scenario is None and st.next_wake is None
    assert f.settings.current.engine.paused
    await f.resume(by="alice")
    assert not f.status().paused
    assert [code for _, code, _ in rec.items] == ["engine_paused", "engine_resumed"]
    assert "alice" in rec.items[0][2]
