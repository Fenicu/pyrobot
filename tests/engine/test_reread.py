import asyncio

import pytest

from app.db.base import Database
from app.db.journal import DbJournal
from app.engine.bus import Bus
from app.engine.clock import SystemClock
from app.engine.host.account import journal_recall, live_reread
from app.engine.memory import MemoryJournal
from app.engine.parsing import default_parser
from app.engine.pipeline import NullReducer, Pipeline
from app.engine.transport.fake import FakeTransport
from app.engine.types import Button
from tests.engine.helpers import GAME, make_msg, now


async def test_reread_goes_through_pipeline_and_becomes_latest() -> None:
    journal = MemoryJournal()
    pipeline = Pipeline(journal=journal, parser=default_parser(), reducer=NullReducer(), bus=Bus())
    task = asyncio.create_task(pipeline.run())
    transport = FakeTransport()
    current = make_msg("🔋88%", msg_id=5, kind="edit", revision=300)
    transport.messages[(GAME, 5)] = current
    try:
        reread = live_reread(transport, pipeline)
        assert await reread(GAME, 5) == current
        assert [msg for msg, _ in journal.rows] == [current]
        assert pipeline.latest(GAME, 5) == current
        # Та же ревизия ещё раз: журнал её не дублирует, а текущей она остаётся.
        assert await reread(GAME, 5) == current
        assert len(journal.rows) == 1 and pipeline.latest(GAME, 5) == current
        assert await reread(GAME, 6) is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.db
async def test_click_on_evicted_message_recalled_from_journal(clean_db: Database) -> None:
    from datetime import timedelta

    from app.engine.gateway.gateway import ActionGateway
    from app.engine.gateway.types import ActionKind, ActionRequest
    from app.engine.memory import MemoryActionStore
    from app.engine.settings import StaticSettings
    from tests.engine.gateway_rig import LIVE, expect_text

    started = now()
    journal = DbJournal(clean_db, 1)
    bus = Bus()
    pipeline = Pipeline(
        journal=journal,
        parser=default_parser(),
        reducer=NullReducer(),
        bus=bus,
        latest_capacity=2,
        recall=journal_recall(journal, received_since=started),
    )
    transport = FakeTransport()
    gateway = ActionGateway(
        transport=transport,
        store=MemoryActionStore(),
        settings=StaticSettings(LIVE.model_copy(deep=True)),
        latest=pipeline.latest,
        boundary=lambda: pipeline.last_journal_id,
        clock=SystemClock(),
        reread=live_reread(transport, pipeline),
        recall=pipeline.recall,
    )
    bus.subscribe(gateway.on_delivery, priority=0)
    tasks = [asyncio.create_task(pipeline.run()), asyncio.create_task(gateway.run())]
    btn = (Button("⬅️", 0, 0, data="maze_left"),)
    # Записано прошлым процессом: текущую версию знает только Telegram.
    old = make_msg("старое", msg_id=1, buttons=btn, received_at=started - timedelta(hours=1))
    assert await journal.append(old, [], None, 0) is not None
    transport.messages[(GAME, 1)] = old
    for msg_id in (2, 3, 4):
        await pipeline.process(make_msg(f"m{msg_id}", msg_id=msg_id, buttons=btn))
    assert pipeline.latest(GAME, 2) is None

    def click(msg_id: int) -> ActionRequest:
        return ActionRequest(
            kind=ActionKind.CLICK,
            chat_id=GAME,
            message_id=msg_id,
            data="maze_left",
            expect=expect_text("x", timeout=0.05),
            expect_revision=0,
        )

    try:
        recalled = await gateway.submit(click(2))
        assert recalled.reason != "stale_button"
        assert transport.fetches == [] and pipeline.latest(GAME, 2) is not None
        reread = await gateway.submit(click(1))
        assert reread.reason != "stale_button"
        assert transport.fetches == [(GAME, 1)]
        assert [(s.kind, s.message_id) for s in transport.sent] == [("click", 2), ("click", 1)]
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
