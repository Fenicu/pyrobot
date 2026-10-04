import asyncio
import itertools
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import timedelta

import pytest

from app.engine.bus import Bus
from app.engine.clock import SystemClock
from app.engine.commands import CommandClass
from app.engine.gateway.gateway import RECONCILE_REASON
from app.engine.gateway.store import Obligation
from app.engine.gateway.types import (
    ActionKind,
    ActionRequest,
    ActionStatus,
    Expectation,
    Source,
)
from app.engine.memory import MemoryJournal
from app.engine.notify import Level
from app.engine.parsing import default_parser
from app.engine.pipeline import NullReducer, Pipeline
from app.engine.reconcile import (
    FOOD,
    GIFTS,
    GORBUSHKA,
    INVENTORY,
    PROFILE,
    Reconciler,
    sources_for,
)
from app.engine.settings import ChatsSection, Settings, StaticSettings
from app.engine.state.reducer import StateReducer
from app.engine.transport.fake import Sent
from tests.engine.gateway_rig import LIVE, Rig, send
from tests.engine.helpers import GAME, now, until
from tests.fixtures import game_msg

PARSER = default_parser(ChatsSection())
ANSWERS = {
    "/compact": ("profile", 3624478),
    "/to_eat": ("food", 3624997),
    "/inv": ("items", 3625102),
    "/gifts": ("items", 3623585),
    "/gorbushka": ("gorbushka", 3516741),
}


class Recorder:
    def __init__(self) -> None:
        self.codes: list[str] = []

    async def notify(self, level: Level, code: str, text: str) -> None:
        self.codes.append(code)


class World:
    def __init__(
        self,
        *,
        apply_state: bool = True,
        answer: bool = True,
        ready: Callable[[], bool] = lambda: True,
    ) -> None:
        self.rig = Rig()
        self.bus = Bus()
        self.pipeline = Pipeline(
            journal=MemoryJournal(),
            parser=PARSER,
            reducer=StateReducer() if apply_state else NullReducer(),
            bus=self.bus,
        )
        self.bus.subscribe(self.rig.gw.on_delivery, priority=0)
        self.notes = Recorder()
        self.ids = itertools.count(5_000_000)
        self.answer = answer
        self.rig.transport.responder = self._respond
        settings = StaticSettings(
            Settings(engine=LIVE.engine.model_copy(update={"refresh_min_interval_s": 0.02}))
        )
        self.reconciler = Reconciler(
            gateway=self.rig.gw,
            store=self.rig.store,
            state=lambda: self.pipeline.state,
            notifier=self.notes,
            settings=settings,
            clock=SystemClock(),
            ready=ready,
            game_chat_id=GAME,
            poll_s=0.01,
            max_backoff_s=0.08,
            timeout_s=0.1,
        )
        self.rig.gw.on_uncertain = self.reconciler.note

    async def _respond(self, rec: Sent) -> None:
        if not self.answer or rec.payload not in ANSWERS:
            return
        family, msg_id = ANSWERS[rec.payload]
        moment = now()
        msg = replace(
            game_msg(family, msg_id),
            msg_id=next(self.ids),
            date=moment,
            created_at=moment,
            received_at=moment,
        )
        await self.pipeline.process(msg)

    def sent(self) -> list[str]:
        return [s.payload for s in self.rig.transport.sent]


@pytest.fixture
async def world() -> AsyncIterator[World]:
    w = World()
    w.rig.start()
    task = asyncio.create_task(w.reconciler.run())
    yield w
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await w.rig.stop()


async def _uncertain(w: World, text: str) -> None:
    res = await w.rig.gw.submit(send(text, expect=Expectation(lambda d: None, 0.05)))
    assert res.status is ActionStatus.OUTCOME_UNKNOWN


def test_sources_mapping() -> None:
    def ob(text: str | None = None, data: str | None = None) -> Obligation:
        return Obligation(1, "click" if data else "send", text, data)

    assert sources_for(ob("/harvest")) == (PROFILE,)
    assert sources_for(ob("🌭Хот-дог")) == (PROFILE, FOOD)
    assert sources_for(ob("/read_exp")) == (PROFILE, INVENTORY)
    assert sources_for(ob("/unbox_ls")) == (PROFILE, GIFTS)
    assert sources_for(ob(data="gorbushka_fight")) == (PROFILE, GORBUSHKA)


async def test_uncertain_spending_blocks_until_sources_refreshed(world: World) -> None:
    await _uncertain(world, "/read_exp")
    assert world.rig.gw.spending_blocked == RECONCILE_REASON
    await until(lambda: world.rig.gw.spending_blocked is None)
    assert world.sent() == ["/read_exp", "/compact", "/inv"]
    assert await world.rig.store.unreconciled() == []
    await until(lambda: world.notes.codes == ["reconciled_auto"])


async def test_blocked_action_rejected_meanwhile() -> None:
    w = World(answer=False)
    w.rig.start()
    try:
        await _uncertain(w, "/harvest")
        res = await w.rig.gw.submit(send("/job", expect=Expectation(lambda d: None, 0.05)))
        assert (res.status, res.reason) == (ActionStatus.REJECTED, f"blocked:{RECONCILE_REASON}")
    finally:
        await w.rig.stop()


async def test_food_uncertainty_refreshes_menu(world: World) -> None:
    await _uncertain(world, "🌭Хот-дог")
    await until(lambda: world.rig.gw.spending_blocked is None)
    assert world.sent()[1:] == ["/compact", "/to_eat"]


async def test_nav_timeout_does_not_block(world: World) -> None:
    res = await world.rig.gw.submit(send("/inv", expect=Expectation(lambda d: None, 0.05)))
    assert res.status is ActionStatus.OUTCOME_UNKNOWN
    assert world.rig.gw.spending_blocked is None


async def test_event_without_state_update_keeps_block_and_backs_off() -> None:
    w = World(apply_state=False)
    w.rig.start()
    task = asyncio.create_task(w.reconciler.run())
    try:
        await _uncertain(w, "/harvest")
        await until(lambda: "reconcile_stuck" in w.notes.codes, timeout=2.0)
        assert w.rig.gw.spending_blocked == RECONCILE_REASON
        await asyncio.sleep(0.3)
        profile_requests = w.sent().count("/compact")
        assert 3 <= profile_requests <= 8
        assert w.notes.codes.count("reconcile_stuck") == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await w.rig.stop()


async def test_obligation_from_restart_is_reconciled(world: World) -> None:
    store = world.rig.store
    req = send("/harvest", source=Source.PLANNER)
    action_id = await store.create(req, CommandClass.ACTION, ActionStatus.SENT)
    await store.mark_unfinished_unknown()
    assert [o.action_id for o in await store.unreconciled()] == [action_id]
    world.rig.gw.block_spending(RECONCILE_REASON)
    await until(lambda: world.rig.gw.spending_blocked is None)
    assert await store.unreconciled() == []


async def test_spend_free_click_from_restart_is_not_an_obligation(world: World) -> None:
    store = world.rig.store
    move = ActionRequest(kind=ActionKind.CLICK, chat_id=GAME, message_id=7, data="maze_left")
    await store.create(move, CommandClass.ACTION, ActionStatus.SENT)
    await store.mark_unfinished_unknown()
    assert await store.unreconciled() == []


async def test_new_obligation_during_reconcile_keeps_block(world: World) -> None:
    store = world.rig.store
    first = True

    async def respond(rec: Sent) -> None:
        nonlocal first
        if rec.payload == "/compact" and first:
            first = False
            late = await store.create(send("/job"), CommandClass.ACTION, ActionStatus.SENT)
            await store.update(late, status=ActionStatus.OUTCOME_UNKNOWN, reason="timeout")
        await World._respond(world, rec)

    world.rig.transport.responder = respond
    await _uncertain(world, "/harvest")
    await until(lambda: world.rig.gw.spending_blocked is None, timeout=2.0)
    assert world.sent().count("/compact") >= 2
    assert await store.unreconciled() == []


async def test_override_clears_everything(world: World) -> None:
    world.answer = False
    await _uncertain(world, "/harvest")
    await world.reconciler.override()
    assert world.rig.gw.spending_blocked is None
    assert await world.rig.store.unreconciled() == []


async def test_note_during_override_keeps_block(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.answer = False
    await _uncertain(world, "/harvest")
    store = world.rig.store
    mark = store.mark_reconciled

    async def racing(ids: list[int]) -> None:
        # Новое неопределённое действие приходит, пока override закрывает старые.
        world.rig.gw.block_spending(RECONCILE_REASON)
        world.reconciler.note(send("/job"), None)
        await mark(ids)

    monkeypatch.setattr(store, "mark_reconciled", racing)
    await world.reconciler.override()
    assert world.rig.gw.spending_blocked == RECONCILE_REASON
    assert [o.text for o in await world.reconciler.pending()] == ["/job"]


async def test_final_check_catches_field_spoiled_by_later_screen(world: World) -> None:
    spoiled = False

    async def respond(rec: Sent) -> None:
        nonlocal spoiled
        if rec.payload == "/inv" and not spoiled:
            spoiled = True
            # Итог, созданный до свежего профиля, делает его money сомнительным.
            moment = now()
            card = replace(
                game_msg("items", 3516678),
                msg_id=next(world.ids),
                date=moment,
                created_at=moment - timedelta(minutes=1),
                received_at=moment,
            )
            await world.pipeline.process(card)
            assert world.pipeline.state["money"]["src"] == "doubtful"
        await World._respond(world, rec)

    world.rig.transport.responder = respond
    await _uncertain(world, "/read_exp")
    await until(lambda: world.rig.gw.spending_blocked is None, timeout=2.0)
    assert spoiled
    assert world.sent().count("/compact") >= 2


async def test_shutdown_is_not_a_reconcile_failure() -> None:
    ready = False
    w = World(ready=lambda: ready)
    w.rig.start()
    task = asyncio.create_task(w.reconciler.run())
    try:
        await _uncertain(w, "/harvest")
        await w.rig.gw.shutdown()
        ready = True
        await asyncio.sleep(0.3)
        assert w.rig.gw.spending_blocked == RECONCILE_REASON
        assert "reconcile_stuck" not in w.notes.codes
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await w.rig.stop()


async def test_not_ready_waits() -> None:
    ready = False
    w = World(ready=lambda: ready)
    w.rig.start()
    task = asyncio.create_task(w.reconciler.run())
    try:
        await _uncertain(w, "/harvest")
        await asyncio.sleep(0.05)
        assert w.sent() == ["/harvest"]
        ready = True
        await until(lambda: w.rig.gw.spending_blocked is None)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await w.rig.stop()
