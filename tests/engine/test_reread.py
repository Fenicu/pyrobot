import asyncio

from app.engine.bus import Bus
from app.engine.host.account import live_reread
from app.engine.memory import MemoryJournal
from app.engine.parsing import default_parser
from app.engine.pipeline import NullReducer, Pipeline
from app.engine.transport.fake import FakeTransport
from tests.engine.helpers import GAME, make_msg


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
