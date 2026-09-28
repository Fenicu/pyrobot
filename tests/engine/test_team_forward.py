"""Пересылка в чат команды итога задания: реакция ставит пересылку в очередь, шлёт её задача."""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.engine.bus import Delivery
from app.engine.gateway.types import ActionStatus
from app.engine.notify import Level
from app.engine.parsing import default_parser
from app.engine.settings import ChatsSection, Settings
from app.engine.team_forward import TeamForward, forward_key
from app.engine.types import IncomingMessage
from tests.engine.gateway_rig import LIVE, Rig
from tests.engine.helpers import GAME, make_msg, until
from tests.fixtures import game_msg

TEAM = -1001149209877
TEAM_LIVE = Settings(
    engine=LIVE.engine, chats=LIVE.chats.model_copy(update={"team_chat_id": TEAM})
)
TASK = ("daily", 3625831)


class Frozen:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return time.monotonic()


class Notes:
    def __init__(self) -> None:
        self.items: list[tuple[Level, str]] = []

    async def notify(self, level: Level, code: str, text: str) -> None:
        self.items.append((level, code))


class ForwardRig:
    def __init__(self, settings: Settings = TEAM_LIVE) -> None:
        self.gw = Rig(settings)
        self.notes = Notes()
        task = game_msg(*TASK)
        self.clock = Frozen(task.origin + timedelta(seconds=2))
        self.reaction = TeamForward(
            gateway=self.gw.gw,
            settings=self.gw.settings,
            notifier=self.notes,
            clock=self.clock,
        )
        self.tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self.gw.start()
        self.tasks.append(asyncio.create_task(self.reaction.run()))

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.gw.stop()

    async def deliver(self, msg: IncomingMessage, *, reactable: bool = True) -> None:
        events = tuple(default_parser(ChatsSection()).parse(msg))
        await self.reaction.on_delivery(Delivery(msg, events, 0, 1, reactable))

    @property
    def sent(self) -> list[tuple[str, int, int | None]]:
        return [(s.kind, s.chat_id, s.message_id) for s in self.gw.transport.sent]

    async def settle(self) -> None:
        await until(lambda: self.reaction.idle)


@pytest.fixture
async def rig() -> AsyncIterator[ForwardRig]:
    r = ForwardRig()
    r.start()
    try:
        yield r
    finally:
        await r.stop()


async def _update(rig: ForwardRig, section: str, **values: object) -> None:
    await rig.gw.settings.update(
        lambda s: s.model_copy(update={section: getattr(s, section).model_copy(update=values)}),
        changed_by="test",
    )


def test_key_only_for_task_completed() -> None:
    task = game_msg(*TASK)
    events = default_parser(ChatsSection()).parse(task)
    assert forward_key(task, events) == f"forward:{GAME}:{task.msg_id}"
    other = game_msg("items", 3516680)
    assert forward_key(other, default_parser(ChatsSection()).parse(other)) is None


async def test_task_completed_forwarded_once(rig: ForwardRig) -> None:
    task = game_msg(*TASK)
    await rig.deliver(task)
    await rig.deliver(task)
    await until(lambda: len(rig.sent) == 1)
    await rig.settle()
    await rig.deliver(task)
    await rig.settle()
    assert rig.sent == [("forward", TEAM, task.msg_id)]
    [row] = rig.gw.store.rows.values()
    assert row.status is ActionStatus.CONFIRMED
    assert row.req.idempotency_key == f"forward:{GAME}:{task.msg_id}"
    assert row.req.from_chat_id == GAME
    assert rig.notes.items == []


async def test_subscriber_does_not_wait_for_gateway() -> None:
    r = ForwardRig()
    await asyncio.wait_for(r.deliver(game_msg(*TASK)), 0.05)
    assert r.gw.transport.sent == [] and r.reaction.queued == 1


async def test_edits_and_revisions_not_forwarded(rig: ForwardRig) -> None:
    task = game_msg(*TASK)
    await rig.deliver(replace(task, revision=int(task.date.timestamp()), kind="edit"))
    await rig.deliver(replace(task, revision=5))
    await rig.settle()
    assert rig.sent == [] and rig.reaction.queued == 0


async def test_other_messages_and_chats_not_forwarded(rig: ForwardRig) -> None:
    task = game_msg(*TASK)
    await rig.deliver(replace(task, chat_id=-1001109615116))
    await rig.deliver(replace(task, outgoing=True))
    await rig.deliver(make_msg("Ты открыл Малый 🗳контейнер.\n\nВнутри ты обнаружил:\nФлюс"))
    await rig.settle()
    assert rig.sent == []


async def test_not_reactable_not_forwarded(rig: ForwardRig) -> None:
    await rig.deliver(game_msg(*TASK), reactable=False)
    await rig.settle()
    assert rig.sent == []


async def test_old_by_origin_not_forwarded(rig: ForwardRig) -> None:
    task = game_msg(*TASK)
    rig.clock.at = task.origin + timedelta(minutes=10, seconds=1)
    await rig.deliver(replace(task, received_at=rig.clock.at))
    await rig.settle()
    assert rig.sent == []


async def test_age_checked_again_before_send() -> None:
    r = ForwardRig()
    task = game_msg(*TASK)
    r.clock.at = task.origin + timedelta(minutes=9)
    await r.deliver(task)
    assert r.reaction.queued == 1
    r.clock.at = task.origin + timedelta(minutes=10, seconds=1)
    r.start()
    try:
        await r.settle()
    finally:
        await r.stop()
    assert r.gw.transport.sent == [] and r.gw.store.rows == {}


async def test_team_chat_off_not_queued(rig: ForwardRig) -> None:
    await _update(rig, "chats", team_chat_id=None)
    await rig.deliver(game_msg(*TASK))
    await rig.settle()
    assert rig.sent == [] and rig.gw.store.rows == {}


async def test_team_chat_changed_before_send() -> None:
    r = ForwardRig()
    await r.deliver(game_msg(*TASK))
    await _update(r, "chats", team_chat_id=-1002222222222)
    r.start()
    try:
        await r.settle()
    finally:
        await r.stop()
    [(kind, chat, _)] = r.sent
    assert (kind, chat) == ("forward", -1002222222222)


async def test_team_chat_changed_in_gateway_queue_rejected() -> None:
    r = ForwardRig()
    r.tasks.append(asyncio.create_task(r.reaction.run()))
    try:
        await r.deliver(game_msg(*TASK))
        await until(lambda: r.gw.gw.queue_size == 1)
        await _update(r, "chats", team_chat_id=-1002222222222)
        r.gw.start()
        await r.settle()
    finally:
        await r.stop()
    assert r.gw.transport.sent == []
    [row] = r.gw.store.rows.values()
    assert (row.status, row.reason) == (ActionStatus.REJECTED, "team_chat_changed")
    assert r.notes.items == []


async def test_dry_run_suppressed(rig: ForwardRig) -> None:
    await _update(rig, "engine", mode="dry_run")
    await rig.deliver(game_msg(*TASK))
    await rig.settle()
    assert rig.sent == []
    [row] = rig.gw.store.rows.values()
    assert (row.status, row.reason) == (ActionStatus.SUPPRESSED, "dry_run")


async def test_kill_rejects_pause_sends(rig: ForwardRig) -> None:
    task = game_msg(*TASK)
    await rig.gw.gw.kill("test")
    await rig.deliver(task)
    await rig.settle()
    assert rig.sent == []
    await rig.gw.gw.unkill()
    await _update(rig, "engine", paused=True)
    await rig.deliver(task)
    await rig.settle()
    assert rig.sent == [("forward", TEAM, task.msg_id)]


async def test_unknown_outcome_notified_not_retried(rig: ForwardRig) -> None:
    task = game_msg(*TASK)
    rig.gw.transport.fail_with.append(TimeoutError("timed out"))
    await rig.deliver(task)
    await rig.settle()
    await rig.deliver(task)
    await rig.settle()
    assert rig.sent == []
    [row] = rig.gw.store.rows.values()
    assert row.status is ActionStatus.OUTCOME_UNKNOWN
    assert rig.notes.items == [("warn", "team_forward_unknown")]
    assert rig.gw.gw.spending_blocked is None


async def test_refused_notified(rig: ForwardRig) -> None:
    from app.engine.transport.base import TransportRejected

    rig.gw.transport.fail_with.append(TransportRejected("CHAT_WRITE_FORBIDDEN"))
    await rig.deliver(game_msg(*TASK))
    await rig.settle()
    assert rig.notes.items == [("warn", "team_forward_failed")]


async def test_recovered_fresh_forwarded(rig: ForwardRig) -> None:
    task = game_msg(*TASK)
    late = replace(task, recovered=True, received_at=task.origin + timedelta(minutes=3))
    rig.clock.at = late.received_at
    await rig.deliver(late)
    await until(lambda: len(rig.sent) == 1)


def test_origin_is_creation_time() -> None:
    created = datetime(2026, 9, 27, 10, 54, 54, tzinfo=UTC)
    msg = replace(make_msg("x", date=created + timedelta(minutes=30)), created_at=created)
    assert msg.origin == created


async def test_through_pipeline_once(rig: ForwardRig) -> None:
    from app.engine.bus import Bus
    from app.engine.memory import MemoryJournal
    from app.engine.pipeline import Pipeline
    from app.engine.state.reducer import StateReducer

    bus = Bus()
    bus.subscribe(rig.reaction.on_delivery, priority=30)
    pipeline = Pipeline(
        journal=MemoryJournal(),
        parser=default_parser(ChatsSection()),
        reducer=StateReducer(),
        bus=bus,
    )
    task = game_msg(*TASK)
    await pipeline.process(task)
    await pipeline.process(task)
    # Догон отдаёт правленое сообщение как новое, но с ревизией правки.
    edited = replace(task, msg_id=task.msg_id + 1, revision=int(task.date.timestamp()) + 60)
    await pipeline.process(edited)
    await until(lambda: len(rig.sent) == 1)
    await rig.settle()
    assert rig.sent == [("forward", TEAM, task.msg_id)]


def _report(msg_id: int, text_day: str | None = None) -> IncomingMessage:
    """Отчёт о фабрике из корпуса; `text_day` — подменить дату битвы в тексте («12.09.26»)."""
    msg = game_msg("crew", msg_id)
    if text_day is None:
        return msg
    import re

    text = re.sub(r"фабрику \d+\.\d+\.\d+:", f"фабрику {text_day}:", msg.text or "")
    return replace(msg, text=text)


async def test_today_factory_report_forwarded_once_per_day(rig: ForwardRig) -> None:
    # 3620025 пришёл 12.09 в 03:12 MSK; с датой битвы 12.09 это сегодняшний отчёт.
    first = _report(3620025, "12.09.26")
    rig.clock.at = first.origin + timedelta(seconds=3)
    events = default_parser(ChatsSection()).parse(first)
    assert forward_key(first, events) == "forward:factory:2026-09-12"
    await rig.deliver(first)
    await until(lambda: len(rig.sent) == 1)
    await rig.settle()
    # Второй /fb тем же днём — другое сообщение с тем же отчётом: ключ дня его не пускает.
    second = replace(first, msg_id=first.msg_id + 7)
    await rig.deliver(second)
    await rig.settle()
    assert rig.sent == [("forward", TEAM, first.msg_id)]
    [row] = rig.gw.store.rows.values()
    assert row.req.idempotency_key == "forward:factory:2026-09-12"


async def test_factory_report_of_other_day_not_forwarded(rig: ForwardRig) -> None:
    # 26.09 02:23 /fb отдал отчёт о битве 25.09.
    old = _report(3625108)
    rig.clock.at = old.origin + timedelta(seconds=3)
    assert forward_key(old, default_parser(ChatsSection()).parse(old)) is None
    await rig.deliver(old)
    await rig.settle()
    assert rig.sent == []


async def test_unverified_team_chat_notified(rig: ForwardRig) -> None:
    rig.gw.transport.groups[TEAM] = "not_member"
    await rig.deliver(game_msg(*TASK))
    await rig.settle()
    assert rig.sent == []
    assert rig.notes.items == [("warn", "team_forward_failed")]
