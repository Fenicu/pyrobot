import asyncio
from collections.abc import Awaitable, Callable

from app.engine.notify import Level
from app.engine.supervisor import Supervisor
from tests.engine.helpers import until


class Recorder:
    def __init__(self) -> None:
        self.codes: list[str] = []

    async def notify(self, level: Level, code: str, text: str) -> None:
        self.codes.append(code)


async def test_restarts_crashed_task_and_notifies() -> None:
    rec = Recorder()
    sup = Supervisor(rec, base_s=0.01, max_s=0.05)
    runs = 0

    async def job() -> None:
        nonlocal runs
        runs += 1
        if runs == 1:
            raise RuntimeError("boom")
        await asyncio.sleep(10)

    sup.start("job", job)
    await until(lambda: runs == 2)
    await until(sup.healthy)
    assert rec.codes == ["task_failed:job"]
    await sup.stop()
    assert not sup.healthy()


async def test_cancel_stops_one_task_and_forgets_it() -> None:
    sup = Supervisor(Recorder(), base_s=0.01, max_s=0.05)
    started: list[str] = []
    cancelled: list[str] = []

    def job(name: str) -> Callable[[], Awaitable[None]]:
        async def run() -> None:
            started.append(name)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(name)
                raise

        return run

    sup.start("planner", job("planner"))
    sup.start("gateway", job("gateway"))
    await until(lambda: len(started) == 2)
    await sup.cancel("planner")
    assert cancelled == ["planner"]
    assert sup.healthy()
    await sup.cancel("missing")
    await sup.stop()
    assert cancelled == ["planner", "gateway"]
