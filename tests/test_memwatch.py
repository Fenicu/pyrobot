import logging
import platform
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import memwatch
from app.memwatch import InFlight, InFlightMiddleware, MemWatch, load_malloc_trim, parse_status

STATUS = """Name:\tpython
VmPeak:\t  612340 kB
VmHWM:\t  434176 kB
VmRSS:\t  378880 kB
RssAnon:\t  300000 kB
Threads:\t10
"""


def _status(rss_kb: int, hwm_kb: int = 500_000, threads: int = 7) -> str:
    return f"VmHWM:\t{hwm_kb} kB\nVmRSS:\t{rss_kb} kB\nThreads:\t{threads}\n"


class Reader:
    """Подменный `/proc/self/status`: значения RSS по очереди, последнее повторяется."""

    def __init__(self, *rss_kb: int) -> None:
        self.values = list(rss_kb)

    def __call__(self) -> str:
        value = self.values.pop(0) if len(self.values) > 1 else self.values[0]
        return _status(value)


def test_parse_status() -> None:
    st = parse_status(STATUS)
    assert (st.rss_kb, st.hwm_kb, st.threads) == (378880, 434176, 10)


def test_samples_capped() -> None:
    watch = MemWatch(read_status=Reader(1000), trim_fn=None, max_samples=3)
    for _ in range(5):
        watch.sample()
    assert len(watch.samples) == 3


def test_jump_logs_delta_and_inflight_paths(caplog: pytest.LogCaptureFixture) -> None:
    watch = MemWatch(read_status=Reader(100_000, 150_000), trim_fn=None, grace_s=0)
    watch.inflight.start(1, "GET", "/api/v1/accounts/1/state")
    watch.inflight.start(2, "GET", "/api/v1/accounts/1/events")
    with caplog.at_level(logging.WARNING, logger="app.memwatch"):
        watch.sample()
        assert not caplog.records
        watch.sample()
    [record] = caplog.records
    msg = record.getMessage()
    assert msg.startswith("memory_jump ")
    assert "delta_mb=48.8" in msg
    assert "rss_mb=146.5" in msg
    assert "/api/v1/accounts/1/state" in msg
    assert "/events" not in msg


def test_small_growth_is_not_a_jump(caplog: pytest.LogCaptureFixture) -> None:
    watch = MemWatch(read_status=Reader(100_000, 140_000), trim_fn=None, grace_s=0)
    with caplog.at_level(logging.WARNING, logger="app.memwatch"):
        watch.sample()
        watch.sample()
    assert not caplog.records


def test_jump_is_silent_during_startup_grace(caplog: pytest.LogCaptureFixture) -> None:
    now = [1000.0]
    watch = MemWatch(
        read_status=Reader(100_000, 200_000, 300_000, 400_000),
        trim_fn=None,
        clock=lambda: now[0],
    )
    with caplog.at_level(logging.WARNING, logger="app.memwatch"):
        watch.sample()
        now[0] += 60
        watch.sample()
        now[0] += memwatch.GRACE_S - 61
        watch.sample()
        assert not caplog.records
        assert [rss for _, rss in watch.samples] == [100_000, 200_000, 300_000]
        # Грейс кончился ровно на пятой минуте: следующий скачок виден.
        now[0] += 1
        watch.sample()
    [record] = caplog.records
    assert "delta_mb=97.7" in record.getMessage()


def test_trim_without_libc_is_noop() -> None:
    watch = MemWatch(read_status=Reader(1000), trim_fn=None)
    watch.trim()
    assert watch.trim_last_freed_kb is None
    assert watch.trim_total_freed_kb == 0
    assert watch.trim_last_at is None


@pytest.mark.skipif(platform.libc_ver()[0] != "glibc", reason="needs glibc")
def test_malloc_trim_loaded_from_glibc() -> None:
    trim_fn = load_malloc_trim()
    assert trim_fn is not None
    assert trim_fn() in (0, 1)


def test_malloc_trim_missing_libc(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_libc(name: str) -> None:
        raise OSError(name)

    monkeypatch.setattr(memwatch.ctypes, "CDLL", no_libc)
    assert load_malloc_trim() is None


def test_trim_records_freed_amount() -> None:
    calls: list[int] = []
    reader = Reader(200_000, 150_000, 150_000, 150_000)

    def trim_fn() -> int:
        calls.append(1)
        return 1

    watch = MemWatch(read_status=reader, trim_fn=trim_fn)
    watch.trim()
    assert calls == [1]
    assert watch.trim_last_freed_kb == 50_000
    assert watch.trim_total_freed_kb == 50_000
    assert watch.trim_last_at is not None
    # RSS после вызова не меньше — освобождено 0, итог не уменьшается.
    watch.trim()
    assert watch.trim_last_freed_kb == 0
    assert watch.trim_total_freed_kb == 50_000


def test_jump_counts_from_rss_after_trim(caplog: pytest.LogCaptureFixture) -> None:
    # Замер 200 МБ, malloc_trim вернул до 150 МБ, следующий замер 195 МБ: рост на 45 МБ от
    # RSS после trim — скачок, хотя от прошлого замера всего -5 МБ.
    reader = Reader(204_800, 204_800, 153_600, 199_680)
    watch = MemWatch(read_status=reader, trim_fn=lambda: 1, grace_s=0)
    with caplog.at_level(logging.WARNING, logger="app.memwatch"):
        watch.sample()
        watch.trim()
        watch.sample()
    [record] = caplog.records
    assert "delta_mb=45.0" in record.getMessage()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_jump_lists_slow_requests_finished_since_last_sample(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    watch = MemWatch(read_status=Reader(100_000, 150_000, 200_000), trim_fn=None, grace_s=0)
    watch.inflight = InFlight(clock=clock)
    watch.sample()
    watch.inflight.start(1, "GET", "/api/v1/accounts/1/journal")
    watch.inflight.start(2, "POST", "/api/v1/auth/login")
    watch.inflight.start(3, "GET", "/api/v1/accounts/1/events")
    clock.now = 0.5
    watch.inflight.finish(2)
    clock.now = 2.5
    watch.inflight.finish(1)
    watch.inflight.finish(3)
    with caplog.at_level(logging.WARNING, logger="app.memwatch"):
        watch.sample()
        watch.sample()
    first, second = (r.getMessage() for r in caplog.records)
    assert 'finished="GET /api/v1/accounts/1/journal 2.5s"' in first
    assert "/auth/login" not in first and "/events" not in first
    # Список — только с прошлого замера.
    assert 'finished=""' in second


def _app(inflight: InFlight, seen: list[list[str]]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(InFlightMiddleware, inflight=inflight)

    def snapshot() -> None:
        seen.append([f"{m} {p}" for m, p, _ in inflight.snapshot()])

    @app.get("/items/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        snapshot()
        return {"id": item_id}

    @app.get("/boom")
    async def boom() -> None:
        snapshot()
        raise RuntimeError("boom")

    return app


async def _get(app: FastAPI, path: str, check: Callable[[int], bool]) -> None:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path)
    assert check(resp.status_code)


async def test_middleware_tracks_request_during_and_clears_after() -> None:
    inflight = InFlight()
    seen: list[list[str]] = []
    await _get(_app(inflight, seen), "/items/5", lambda s: s == 200)
    assert seen == [["GET /items/{item_id}"]]
    assert inflight.snapshot() == []


async def test_middleware_clears_after_exception() -> None:
    inflight = InFlight()
    seen: list[list[str]] = []
    await _get(_app(inflight, seen), "/boom", lambda s: s == 500)
    assert seen == [["GET /boom"]]
    assert inflight.snapshot() == []
