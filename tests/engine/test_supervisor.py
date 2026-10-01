import asyncio
from collections.abc import Awaitable, Callable

from app.engine.fence import LeaseLost
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


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def crashing(clock: Clock, step_s: float, runs: list[int]) -> Callable[[], Awaitable[None]]:
    """Задача, которая падает сразу, сдвигая часы супервизора на `step_s` перед каждым сбоем."""

    async def run() -> None:
        runs.append(len(runs) + 1)
        clock.now += step_s
        raise RuntimeError("boom")

    return run


async def test_crash_loop_after_five_failures_in_window() -> None:
    rec = Recorder()
    clock = Clock()
    loops: list[str] = []

    async def on_crash_loop(name: str) -> None:
        loops.append(name)

    sup = Supervisor(rec, base_s=0.001, max_s=0.001, on_crash_loop=on_crash_loop, monotonic=clock)
    runs: list[int] = []
    # Пять сбоев за 400 с — в окне 600 с.
    sup.start("gateway", crashing(clock, 100.0, runs))
    await until(lambda: loops == ["gateway"])
    await asyncio.sleep(0.05)
    assert loops == ["gateway"]
    assert len(runs) == 5
    assert rec.codes == ["task_failed:gateway"] * 5
    assert not sup.healthy()
    await sup.stop()


async def test_failures_outside_window_do_not_count() -> None:
    clock = Clock()
    loops: list[str] = []

    async def on_crash_loop(name: str) -> None:
        loops.append(name)

    sup = Supervisor(
        Recorder(), base_s=0.001, max_s=0.001, on_crash_loop=on_crash_loop, monotonic=clock
    )
    runs: list[int] = []
    # Сбои через 150 с: сбой ровно 600-секундной давности уже вне окна, в окне — не больше 4.
    sup.start("gateway", crashing(clock, 150.0, runs))
    await until(lambda: len(runs) >= 12)
    assert loops == []
    await sup.stop()


async def test_without_crash_limit_restarts_forever() -> None:
    # Супервизор процесса: задача процесса не бросается после серии сбоев.
    clock = Clock()
    loops: list[str] = []

    async def on_crash_loop(name: str) -> None:
        loops.append(name)

    sup = Supervisor(
        Recorder(),
        base_s=0.001,
        max_s=0.001,
        crash_limit=None,
        on_crash_loop=on_crash_loop,
        monotonic=clock,
    )
    runs: list[int] = []
    sup.start("lease", crashing(clock, 1.0, runs))
    await until(lambda: len(runs) >= 12)
    assert loops == [] and not sup._tasks["lease"].done()
    await sup.stop()


async def test_crash_notification_refused_after_lost_lease_does_not_stop_restarts() -> None:
    class Fenced:
        async def notify(self, level: Level, code: str, text: str) -> None:
            raise LeaseLost("account 1 lease lost")

    sup = Supervisor(Fenced(), base_s=0.01, max_s=0.05)
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
    await sup.stop()


async def test_stop_awaits_task_that_fails_while_cancelled() -> None:
    # Запись в finally после потери аренды заменяет отмену на LeaseLost: задача не
    # перезапускается, stop() дожидается всех задач.
    sup = Supervisor(Recorder(), base_s=0.001, max_s=0.001)
    runs: list[str] = []
    done: list[str] = []

    def job(name: str) -> Callable[[], Awaitable[None]]:
        async def run() -> None:
            runs.append(name)
            try:
                await asyncio.Event().wait()
            finally:
                done.append(name)
                if name == "planner":
                    raise LeaseLost("account 1 lease lost")

        return run

    sup.start("planner", job("planner"))
    sup.start("gateway", job("gateway"))
    await until(lambda: len(runs) == 2)
    await asyncio.wait_for(sup.stop(), 1.0)
    assert sorted(done) == ["gateway", "planner"]
    await asyncio.sleep(0.02)
    assert sorted(runs) == ["gateway", "planner"]
