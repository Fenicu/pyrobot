import asyncio
import time
from collections.abc import Callable
from typing import Any

import pytest

from app.engine.bus import Bus
from app.engine.clock import SystemClock
from app.engine.facade import EngineFacade, LockLostError
from app.engine.gateway.gateway import ActionGateway
from app.engine.gateway.store import ActionStore
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus
from app.engine.lag import LoopLagMonitor
from app.engine.memory import MemoryActionStore, MemoryJournal
from app.engine.notify import NotifierPort
from app.engine.parsing import default_parser
from app.engine.pipeline import NullReducer, Pipeline
from app.engine.settings import (
    SettingsChange,
    SettingsConflict,
    SettingsPatchError,
    SettingsProvider,
    StaticSettings,
)
from app.engine.state.model import company_of
from app.engine.tg_auth import TgAuthBackend, TgAuthManager, TgState
from app.engine.transport.fake import FakeTgBackend, FakeTransport
from tests.engine.helpers import GAME, until


def build(
    authorized: bool = True,
    settings: SettingsProvider | None = None,
    lock_ok: Callable[[], bool] = lambda: True,
    backend: TgAuthBackend | None = None,
    notifier: NotifierPort | None = None,
    planner: object | None = None,
    store: ActionStore | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    snapshot: dict[str, Any] | None = None,
) -> EngineFacade:
    """Фасад на памяти; `snapshot` — снимок состояния, его подхватит `pipeline.load()`."""
    settings = settings or StaticSettings()
    bus = Bus()
    journal = MemoryJournal()
    journal.snapshot = (snapshot or {}, 0)
    pipeline = Pipeline(journal=journal, parser=default_parser(), reducer=NullReducer(), bus=bus)
    gateway = ActionGateway(
        transport=FakeTransport(),
        store=store or MemoryActionStore(),
        settings=settings,
        latest=pipeline.latest,
        boundary=lambda: pipeline.last_journal_id,
        clock=SystemClock(),
        state_version=lambda: pipeline.version,
        own_company=lambda: company_of(pipeline.state),
    )
    bus.subscribe(gateway.on_delivery, priority=0)
    tg = TgAuthManager(backend or FakeTgBackend(authorized=authorized), expected_user_id=267519921)
    return EngineFacade(
        settings=settings,
        gateway=gateway,
        pipeline=pipeline,
        tg_auth=tg,
        lag=LoopLagMonitor(),
        lock_ok=lock_ok,
        notifier=notifier,
        planner=planner,  # type: ignore[arg-type]
        monotonic=monotonic,
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


class _Planner:
    current: str | None = None
    next_wake = None

    def __init__(self) -> None:
        self.woken = 0

    def wake(self) -> None:
        self.woken += 1


async def test_patch_settings_wakes_planner_and_audits_mode() -> None:
    rec, planner = _Recorder(), _Planner()
    f = build(notifier=rec, planner=planner)
    upd = await f.patch_settings({"engine": {"min_request_interval_s": 2}}, version=0, by="alice")
    assert upd.version == 1 and upd.changed == {"engine.min_request_interval_s": [1.6, 2.0]}
    assert planner.woken == 1 and rec.items == []
    with pytest.raises(SettingsPatchError) as err:
        await f.patch_settings({"engine": {"mode": "live"}}, version=1, by="alice")
    assert err.value.code == "live_requires_confirm" and f.settings.version == 1
    await f.patch_settings({"engine": {"mode": "live"}}, version=1, by="alice", confirm_live=True)
    assert f.status().mode == "live" and planner.woken == 2
    assert [(lvl, code) for lvl, code, _ in rec.items] == [("info", "engine_mode")]
    assert "dry_run -> live" in rec.items[0][2] and "alice" in rec.items[0][2]
    # Обратно в dry_run подтверждение не нужно.
    await f.patch_settings({"engine": {"mode": "dry_run"}}, version=2, by="bob")
    assert f.status().mode == "dry_run"
    with pytest.raises(SettingsConflict):
        await f.patch_settings({"engine": {"action_ttl_s": 5}}, version=1, by="bob")


async def test_patch_reports_version_it_wrote() -> None:
    settings = StaticSettings()

    class Racing(_Recorder):
        async def notify(self, level: str, code: str, text: str) -> None:
            await super().notify(level, code, text)
            # Другое изменение настроек успевает, пока PATCH пишет аудит.
            await settings.update(lambda s: s, changed_by="other")

    f = build(settings=settings, notifier=Racing())
    upd = await f.patch_settings(
        {"engine": {"mode": "live"}}, version=0, by="admin", confirm_live=True
    )
    assert (upd.version, settings.version) == (1, 2)
