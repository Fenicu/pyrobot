"""Пересылка в чат команды итога задания: реакция ставит пересылку в очередь, шлёт её задача."""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.engine.bus import Delivery
from app.engine.gametime import MSK
from app.engine.gateway.types import ActionStatus
from app.engine.notify import Level
from app.engine.parsing import default_parser
from app.engine.settings import ChatsSection, Settings
from app.engine.team_forward import TeamForward, forward_target
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
        task = game_msg(*TASK)
        # Одни стенные часы у реакции и шлюза: сутки и срок сверяются по ним.
        self.clock = Frozen(task.origin + timedelta(seconds=2))
        self.gw = Rig(settings, clock=self.clock)
        self.notes = Notes()
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
        # Шлюз перечитывает исходное сообщение перед пересылкой: в Telegram оно такое же.
        self.gw.transport.messages.setdefault((msg.chat_id, msg.msg_id), msg)
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
    target = forward_target(task, events)
    assert target is not None and target.key == f"forward:{GAME}:{task.msg_id}"
    assert target.day is None
    other = game_msg("items", 3516680)
    assert forward_target(other, default_parser(ChatsSection()).parse(other)) is None


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
    rig.gw.transport.messages[(task.chat_id, task.msg_id)] = task
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
    target = forward_target(first, events)
    assert target is not None and target.key == "forward:factory:2026-09-12"
    assert target.day == date(2026, 9, 12)
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
    assert forward_target(old, default_parser(ChatsSection()).parse(old)) is None
    await rig.deliver(old)
    await rig.settle()
    assert rig.sent == []


async def test_unverified_team_chat_notified(rig: ForwardRig) -> None:
    rig.gw.transport.groups[TEAM] = "not_member"
    await rig.deliver(game_msg(*TASK))
    await rig.settle()
    assert rig.sent == []
    assert rig.notes.items == [("warn", "team_forward_failed")]


def _late_report(hour: int, minute: int) -> IncomingMessage:
    """Сегодняшний отчёт о фабрике (битва 12.09), созданный 12.09 в `hour:minute` MSK."""
    created = datetime(2026, 9, 12, hour, minute, tzinfo=MSK).astimezone(UTC)
    return replace(_report(3620025, "12.09.26"), date=created, created_at=created)


async def test_factory_report_not_forwarded_after_battle_day() -> None:
    # Отчёт создан в 23:55, очередь дошла до него в 00:02: окно возраста (10 мин) ещё открыто, но
    # сутки битвы кончились — вчерашний отчёт не пересылается.
    r = ForwardRig()
    report = _late_report(23, 55)
    r.clock.at = report.origin + timedelta(seconds=3)
    await r.deliver(report)
    assert r.reaction.queued == 1
    r.clock.at = datetime(2026, 9, 13, 0, 2, tzinfo=MSK).astimezone(UTC)
    r.start()
    try:
        await r.settle()
    finally:
        await r.stop()
    assert r.gw.transport.sent == [] and r.gw.store.rows == {}
    assert r.notes.items == []


async def test_factory_report_ttl_ends_at_midnight(rig: ForwardRig) -> None:
    # В 23:58 окно возраста — ещё почти 10 минут, но в очереди шлюза пересылка живёт до полуночи.
    report = _late_report(23, 57)
    rig.clock.at = report.origin + timedelta(minutes=1)
    await rig.deliver(report)
    await until(lambda: len(rig.sent) == 1)
    await rig.settle()
    [row] = rig.gw.store.rows.values()
    assert row.req.ttl_s == 120


async def test_task_ttl_is_age_window(rig: ForwardRig) -> None:
    task = game_msg(*TASK)
    await rig.deliver(task)
    await until(lambda: len(rig.sent) == 1)
    await rig.settle()
    [row] = rig.gw.store.rows.values()
    assert row.req.ttl_s == 10 * 60 - 2


async def test_source_edited_before_send_refused_and_notified(rig: ForwardRig) -> None:
    task = game_msg(*TASK)
    edited = replace(task, text=(task.text or "") + " ", revision=task.revision + 1)
    rig.gw.transport.messages[(task.chat_id, task.msg_id)] = edited
    await rig.deliver(task)
    await rig.settle()
    assert rig.sent == []
    [row] = rig.gw.store.rows.values()
    assert (row.status, row.reason) == (ActionStatus.REFUSED, "source_changed")
    assert row.req.expect_content == task.content_hash()
    assert rig.notes.items == [("warn", "team_forward_failed")]


async def test_factory_report_not_forwarded_when_read_crosses_midnight(rig: ForwardRig) -> None:
    # Реакция и очередь успели до полуночи, чтение источника начато в 23:59:59, а ответ пришёл в
    # 00:00:01 — день битвы кончился прямо перед вызовом транспорта.
    report = _late_report(23, 59)
    rig.clock.at = datetime(2026, 9, 12, 23, 59, 59, tzinfo=MSK).astimezone(UTC)

    async def midnight() -> None:
        rig.clock.at = datetime(2026, 9, 13, 0, 0, 1, tzinfo=MSK).astimezone(UTC)

    rig.gw.transport.on_fetch = midnight
    await rig.deliver(report)
    await until(lambda: rig.gw.transport.fetches != [])
    await rig.settle()
    assert rig.sent == []
    [row] = rig.gw.store.rows.values()
    assert (row.status, row.reason) == (ActionStatus.REJECTED, "deadline")
    assert row.req.deadline == datetime(2026, 9, 13, tzinfo=MSK).astimezone(UTC)


async def test_task_has_no_deadline(rig: ForwardRig) -> None:
    await rig.deliver(game_msg(*TASK))
    await until(lambda: len(rig.sent) == 1)
    await rig.settle()
    [row] = rig.gw.store.rows.values()
    assert row.req.deadline is None


async def test_cancelled_mid_forward_notified_once_at_next_start() -> None:
    # Шлюз остановлен посреди пересылки (outcome_unknown cancelled): реакция не уведомляет — это
    # сделает следующий старт вместе с закрытием строки, одно уведомление, а не два.
    r = ForwardRig()
    hang = asyncio.Event()

    async def read_forever() -> None:
        await hang.wait()

    r.gw.transport.on_fetch = read_forever
    r.start()
    try:
        await r.deliver(game_msg(*TASK))
        await until(lambda: r.gw.transport.fetches != [])
        await r.gw.stop()
        await r.settle()
    finally:
        await r.stop()
    [row] = r.gw.store.rows.values()
    assert (row.status, row.reason) == (ActionStatus.OUTCOME_UNKNOWN, "cancelled")
    assert r.notes.items == [] and r.sent == []
    await r.gw.store.mark_unfinished_unknown()
    assert [code for _, code, _ in r.gw.store.notes] == ["team_forward_unknown"]
