from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.app import create_api
from app.memwatch import MemWatch
from tests.api.conftest import Api, login, make_user

pytestmark = pytest.mark.db

URL = "/api/v1/admin/memory"


def _status(rss_kb: int) -> str:
    return f"VmHWM:\t409600 kB\nVmRSS:\t{rss_kb} kB\nThreads:\t9\n"


async def test_owner_gets_memory_report(api: Api) -> None:
    rss = iter([307200, 256000, 204800])
    watch = MemWatch(read_status=lambda: _status(next(rss, 204800)), trim_fn=lambda: 0)
    api.container.memwatch = watch
    watch.sample()
    watch.trim()

    resp = await api.client.get(URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"rss_mb", "peak_mb", "threads", "samples", "trim", "top_types"}
    assert (body["rss_mb"], body["peak_mb"], body["threads"]) == (200.0, 400.0, 9)
    [sample] = body["samples"]
    assert sample["rss_mb"] == 300.0
    datetime.fromisoformat(sample["ts"])
    assert body["trim"]["last_freed_mb"] == 50.0
    assert body["trim"]["total_freed_mb"] == 50.0
    datetime.fromisoformat(body["trim"]["last_at"])
    types = body["top_types"]
    assert 0 < len(types) <= 25
    assert all(set(t) == {"type", "count"} for t in types)
    counts = [t["count"] for t in types]
    assert counts == sorted(counts, reverse=True)


async def test_memory_report_before_any_trim(api: Api) -> None:
    api.container.memwatch = MemWatch(read_status=lambda: _status(102400))
    resp = await api.client.get(URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["samples"] == []
    assert body["trim"] == {"last_freed_mb": None, "total_freed_mb": 0.0, "last_at": None}


async def test_memory_report_is_404_for_user_and_401_without_session(api: Api) -> None:
    await make_user(api.container, "bob", role="user")
    app = create_api(api.container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(URL)).status_code == 401
        await login(client, login="bob")
        resp = await client.get(URL)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "not found"
