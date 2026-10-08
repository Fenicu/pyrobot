import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from app.engine.artifact import ArtifactConflict
from app.engine.bus import Bus
from app.engine.clock import SystemClock
from app.engine.facade import (
    EngineFacade,
    GameChatWatch,
    LockLostError,
    TangerinePartner,
    TangerinePost,
    TgNotOnline,
)
from app.engine.gadgets import GadgetConflict
from app.engine.gateway.gateway import ActionGateway
from app.engine.gateway.store import ActionStore
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus
from app.engine.host.account import live_reread
from app.engine.host.codes import CodeLimiter
from app.engine.memory import MemoryActionStore, MemoryJournal
from app.engine.notify import NotifierPort
from app.engine.parsing import default_parser
from app.engine.pipeline import NullReducer, Pipeline
from app.engine.server_settings import EngineBounds
from app.engine.settings import (
    EngineSection,
    Settings,
    SettingsChange,
    SettingsConflict,
    SettingsPatchError,
    SettingsProvider,
    StaticSettings,
    self_chat_fields,
)
from app.engine.state.model import (
    CharacterState,
    GadgetsState,
    GadgetState,
    Obs,
    company_of,
    dump_state,
)
from app.engine.tg_auth import TgAuthBackend, TgState
from app.engine.transport.base import FloodWait, Sender, TransportRejected
from app.engine.transport.fake import FakeTgBackend, FakeTransport
from tests.engine.helpers import GAME, tg_auth, until


def build(
    authorized: bool = True,
    settings: SettingsProvider | None = None,
    lease_ok: Callable[[], bool] = lambda: True,
    backend: TgAuthBackend | None = None,
    notifier: NotifierPort | None = None,
    planner: object | None = None,
    store: ActionStore | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    snapshot: dict[str, Any] | None = None,
    bound_user_id: int | None = 267519921,
    codes: CodeLimiter | None = None,
    transport: FakeTransport | None = None,
    history: GameChatWatch | None = None,
    bounds: Callable[[], EngineBounds] | None = None,
) -> EngineFacade:
    """Фасад на памяти; `snapshot` — снимок состояния, его подхватит `pipeline.load()`. Вход в
    Telegram сверяет свой чат с настройками `settings`."""
    settings = settings or StaticSettings()
    bus = Bus()
    journal = MemoryJournal()
    journal.snapshot = (snapshot or {}, 0)
    pipeline = Pipeline(journal=journal, parser=default_parser(), reducer=NullReducer(), bus=bus)
    transport = transport or FakeTransport()
    gateway = ActionGateway(
        transport=transport,
        store=store or MemoryActionStore(),
        settings=settings,
        latest=pipeline.latest,
        boundary=lambda: pipeline.last_journal_id,
        clock=SystemClock(),
        state_version=lambda: pipeline.version,
        own_company=lambda: company_of(pipeline.state),
        reread=live_reread(transport, pipeline),
    )
    bus.subscribe(gateway.on_delivery, priority=0)
    current = settings
    tg = tg_auth(
        backend or FakeTgBackend(authorized=authorized),
        expected_user_id=bound_user_id,
        self_chat=lambda user_id: self_chat_fields(current.current, user_id),
        codes=codes,
    )
    return EngineFacade(
        settings=settings,
        gateway=gateway,
        pipeline=pipeline,
        tg_auth=tg,
        lease_ok=lease_ok,
        notifier=notifier,
        planner=planner,  # type: ignore[arg-type]
        monotonic=monotonic,
        transport=transport,
        history=lambda: history,
        bounds=bounds,
    )


class Watch:
    """Сверка истории для фасада: членство в общем чате игры и отметки вступления."""

    def __init__(self, member: bool | None = False) -> None:
        self.game_chat_member = member
        self.joined = 0

    def game_chat_joined(self) -> None:
        self.joined += 1
        self.game_chat_member = True


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


async def test_game_chat_member_in_status() -> None:
    assert build().status().game_chat_member is None
    assert build(history=Watch(member=False)).status().game_chat_member is False


async def test_join_game_chat_by_username_with_swinfo_id() -> None:
    transport, watch = FakeTransport(), Watch()
    f = build(transport=transport, history=watch)
    await f.tg.boot()
    assert await f.join_game_chat() == "joined"
    assert transport.joins == [("startupwarschat", -1001109615116)]
    assert watch.joined == 1 and f.status().game_chat_member is True
    transport.join_status = "already_member"
    assert await f.join_game_chat() == "already_member"
    assert watch.joined == 2


async def test_join_game_chat_request_or_refusal_keeps_member() -> None:
    transport, watch = FakeTransport(), Watch()
    f = build(transport=transport, history=watch)
    await f.tg.boot()
    transport.join_status = "request_sent"
    assert await f.join_game_chat() == "request_sent"
    transport.join_fail_with = [TransportRejected("chat_mismatch")]
    with pytest.raises(TransportRejected, match="chat_mismatch"):
        await f.join_game_chat()
    assert watch.joined == 0 and f.status().game_chat_member is False


async def test_join_game_chat_needs_online_telegram() -> None:
    transport = FakeTransport()
    f = build(authorized=False, transport=transport, history=Watch())
    await f.tg.boot()
    with pytest.raises(TgNotOnline):
        await f.join_game_chat()
    assert transport.joins == []


async def test_tangerine_post_joins_by_username_then_sends() -> None:
    transport = FakeTransport()
    f = build(transport=transport)
    await f.tg.boot()
    assert f.settings.current.engine.mode == "dry_run"
    post = await f.tangerine_post("🍊")
    assert transport.joins == [("mandarinkaSW", -1001377961602)]
    assert transport.posted == [(-1001377961602, "🍊")]
    assert post == TangerinePost(chat_id=-1001377961602, message_id=post.message_id)
    assert post.message_id > 0
    assert transport.sent == []


async def test_tangerine_post_uses_chat_from_settings() -> None:
    transport = FakeTransport()
    settings = StaticSettings(Settings.model_validate({"chats": {"tangerine_chat_id": -1009}}))
    f = build(transport=transport, settings=settings)
    await f.tg.boot()
    transport.join_status = "already_member"
    post = await f.tangerine_post("привет")
    assert transport.joins == [("mandarinkaSW", -1009)]
    assert post.chat_id == -1009 and transport.posted == [(-1009, "привет")]


async def test_tangerine_post_mismatch_sends_nothing() -> None:
    transport = FakeTransport()
    transport.chat_ids["mandarinkaSW"] = -1001
    f = build(transport=transport)
    await f.tg.boot()
    with pytest.raises(TransportRejected, match="chat_mismatch"):
        await f.tangerine_post("🍊")
    assert transport.posted == []


async def test_tangerine_post_join_request_pending_sends_nothing() -> None:
    transport = FakeTransport()
    transport.join_status = "request_sent"
    f = build(transport=transport)
    await f.tg.boot()
    with pytest.raises(TransportRejected, match="join_request_sent"):
        await f.tangerine_post("🍊")
    assert transport.posted == []


async def test_tangerine_post_needs_online_telegram() -> None:
    transport = FakeTransport()
    f = build(authorized=False, transport=transport)
    await f.tg.boot()
    with pytest.raises(TgNotOnline):
        await f.tangerine_post("🍊")
    assert transport.joins == [] and transport.posted == []


async def test_tangerine_reply_to_goes_through_patch_with_retry_on_conflict() -> None:
    class Racing(StaticSettings):
        """Первая запись проигрывает гонку: версия успела смениться."""

        raced = False

        async def update(
            self, change: SettingsChange, *, changed_by: str, expected_version: int | None = None
        ) -> tuple[Settings, int]:
            if not self.raced:
                self.raced = True
                await super().update(lambda s: s, changed_by="other")
            return await super().update(
                change, changed_by=changed_by, expected_version=expected_version
            )

    settings = Racing()
    f = build(settings=settings)
    await f.tg.boot()
    upd = await f.set_tangerine_reply_to(777, by="admin")
    assert settings.current.chats.tangerine_reply_to == 777
    assert upd.version == 2 and upd.changed == {"chats.tangerine_reply_to": [None, 777]}


async def test_tangerine_partner_unset_asks_nothing() -> None:
    transport = FakeTransport()
    f = build(authorized=False, transport=transport)
    await f.tg.boot()
    assert await f.tangerine_partner() == TangerinePartner(reply_to=None, sender=None)
    assert transport.sender_lookups == []


async def test_tangerine_partner_cached_until_reply_to_changes() -> None:
    transport = FakeTransport()
    anna = Sender(42, "Анна", None, "anna")
    transport.senders[(-1001377961602, 7)] = anna
    f = build(transport=transport)
    await f.tg.boot()
    await f.set_tangerine_reply_to(7, by="admin")
    assert await f.tangerine_partner() == TangerinePartner(7, anna)
    assert await f.tangerine_partner() == TangerinePartner(7, anna)
    assert transport.sender_lookups == [(-1001377961602, 7)]
    # Сообщения нет — тоже запоминается до смены адресата.
    await f.set_tangerine_reply_to(8, by="admin")
    assert await f.tangerine_partner() == TangerinePartner(8, None)
    assert await f.tangerine_partner() == TangerinePartner(8, None)
    assert transport.sender_lookups == [(-1001377961602, 7), (-1001377961602, 8)]


async def test_tangerine_partner_failure_not_cached() -> None:
    transport = FakeTransport()
    anna = Sender(42, "Анна", None, "anna")
    transport.senders[(-1001377961602, 7)] = anna
    transport.sender_fail_with.append(FloodWait(3))
    f = build(transport=transport)
    await f.tg.boot()
    await f.set_tangerine_reply_to(7, by="admin")
    with pytest.raises(FloodWait):
        await f.tangerine_partner()
    assert await f.tangerine_partner() == TangerinePartner(7, anna)


async def test_tangerine_partner_needs_online_telegram() -> None:
    transport = FakeTransport()
    settings = StaticSettings(Settings.model_validate({"chats": {"tangerine_reply_to": 7}}))
    f = build(authorized=False, transport=transport, settings=settings)
    await f.tg.boot()
    with pytest.raises(TgNotOnline):
        await f.tangerine_partner()
    assert transport.sender_lookups == []


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
    f = build(lease_ok=lambda: held[0])
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
    f = build(lease_ok=lambda: held[0], notifier=rec)
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


async def test_artifact_start_needs_online_tg_and_live_mode() -> None:
    live = StaticSettings(Settings(engine=EngineSection(mode="live")))
    planner = _Planner()
    f = build(settings=live, planner=planner)
    with pytest.raises(ArtifactConflict) as offline:
        await f.artifact_start("light", lottery_max=False, by="alice")
    assert offline.value.code == "tg_not_online"
    dry = build()
    await dry.tg.boot()
    with pytest.raises(ArtifactConflict) as err:
        await dry.artifact_start("light", lottery_max=False, by="alice")
    assert err.value.code == "dry_run"
    await f.tg.boot()
    await f.artifact_start("light", lottery_max=False, by="alice")
    assert f.settings.current.artifact_run.status == "starting"
    assert planner.woken == 1
    await f.artifact_cancel(by="alice")
    assert f.settings.current.artifact_run.status == "idle" and planner.woken == 2


async def test_gadget_upgrade_start_requires_online_and_live() -> None:
    phone = GadgetState(grade="⚪️", level=3, slot="📱", name="Китайская мобила", code="p1")
    seen = Obs(value=GadgetsState(items=(phone,)), at=datetime.now(UTC))
    snapshot = dump_state(CharacterState(gadgets=seen))
    live = StaticSettings(Settings(engine=EngineSection(mode="live")))
    planner = _Planner()
    f = build(settings=live, planner=planner, snapshot=snapshot)
    await f.pipeline.load()
    with pytest.raises(GadgetConflict) as offline:
        await f.gadget_upgrade_start("right", 10, "auto", by="alice")
    assert offline.value.code == "tg_not_online"
    dry = build(snapshot=snapshot)
    await dry.pipeline.load()
    await dry.tg.boot()
    with pytest.raises(GadgetConflict) as err:
        await dry.gadget_upgrade_start("right", 10, "auto", by="alice")
    assert err.value.code == "dry_run"
    assert planner.woken == 0
    await f.tg.boot()
    with pytest.raises(GadgetConflict) as empty:
        await f.gadget_upgrade_start("left", 10, "auto", by="alice")
    assert empty.value.code == "not_worn"
    await f.gadget_upgrade_start("right", 10, "auto", by="alice")
    task = f.settings.current.gadget_upgrade
    assert (task.status, task.task_id, task.gadget, task.start_level) == (
        "active",
        1,
        "Китайская мобила",
        3,
    )
    assert planner.woken == 1
    await f.gadget_upgrade_stop(by="alice")
    assert f.settings.current.gadget_upgrade.status == "stopped" and planner.woken == 2
    with pytest.raises(GadgetConflict) as idle:
        await f.gadget_upgrade_stop(by="alice")
    assert idle.value.code == "no_task"
