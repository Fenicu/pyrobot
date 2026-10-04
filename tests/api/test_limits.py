import asyncio
import contextlib

import pytest
from starlette.requests import ClientDisconnect

from app.api.app import create_api
from tests.api.conftest import Api, run_engine
from tests.api.sse import read_sse
from tests.engine.helpers import until
from tests.engine.test_facade import build

pytestmark = pytest.mark.db
ACCOUNTS = "/api/v1/accounts"


async def test_post_accounts_maps_limit_codes(api: Api) -> None:
    admin = await api.container.auth.get_user("admin")
    assert admin is not None

    # 1. limit_reached: у пользователя max_accounts=1 (уже есть аккаунт 1)
    await api.container.users.set_limit(admin.id, max_accounts=1)
    r = await api.client.post(ACCOUNTS, headers=api.headers, json={"name": "Второй"})
    assert (r.status_code, r.json()) == (409, {"detail": "limit_reached"})

    # Разрешаем пользователю больше аккаунтов
    await api.container.users.set_limit(admin.id, max_accounts=10)

    # 2. server_full: лимит сервера на общее число аккаунтов (аккаунт 1 уже есть)
    api.container.server_settings.current.limits.max_accounts_total = 1
    r = await api.client.post(ACCOUNTS, headers=api.headers, json={"name": "Второй"})
    assert (r.status_code, r.json()) == (409, {"detail": "server_full"})

    # Возвращаем серверный лимит
    api.container.server_settings.current.limits.max_accounts_total = 50

    # 3. capacity_reached: лимит на одновременные движки (аккаунт 1 запущен)
    api.container.config.max_engines = 1
    r = await api.client.post(ACCOUNTS, headers=api.headers, json={"name": "Второй"})
    assert (r.status_code, r.json()) == (409, {"detail": "capacity_reached"})

    # При достаточной ёмкости создание успешно (201)
    api.container.config.max_engines = 10
    r = await api.client.post(ACCOUNTS, headers=api.headers, json={"name": "Второй"})
    assert r.status_code == 201


async def test_sse_over_limit_is_429_and_slot_freed_on_close(api: Api) -> None:
    run_engine(api.container, build(), 1)
    app = create_api(api.container)
    cookie_hdr = {"cookie": f"pyrobot_session={api.client.cookies['pyrobot_session']}"}
    admin = await api.container.auth.get_user("admin")
    assert admin is not None

    # Ограничиваем лимит SSE 1 потоком на пользователя
    api.container.server_settings.current.limits.sse_per_user = 1

    # Запускаем чтение SSE в фоне
    stream_task = asyncio.create_task(
        read_sse(app, "/api/v1/accounts/1/events", headers=cookie_hdr, count=100)
    )

    await until(lambda: api.container.sse_slots.count(admin.id) == 1, 2.0)

    # Второе подключение должно получить 429 too_many_streams
    status_code, _ = await read_sse(app, "/api/v1/accounts/1/events", headers=cookie_hdr, count=1)
    assert status_code == 429

    # Отменяем первый поток
    stream_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await stream_task

    # Слот освобождается
    await until(lambda: api.container.sse_slots.count(admin.id) == 0, 2.0)

    # Теперь новое подключение успешно открывает стрим (200)
    status_code, events = await read_sse(
        app, "/api/v1/accounts/1/events", headers=cookie_hdr, count=1
    )
    assert status_code == 200
    assert len(events) == 1
    assert api.container.sse_slots.count(admin.id) == 0


async def test_sse_slot_freed_when_client_disconnects_before_first_byte(api: Api) -> None:
    run_engine(api.container, build(), 1)
    app = create_api(api.container)
    admin = await api.container.auth.get_user("admin")
    assert admin is not None

    sent_request = False

    async def receive() -> dict[str, object]:
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            raise OSError("client disconnect before first byte")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/accounts/1/events",
        "raw_path": b"/api/v1/accounts/1/events",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"cookie", f"pyrobot_session={api.client.cookies['pyrobot_session']}".encode())
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }

    with pytest.raises((OSError, ClientDisconnect)):
        await app(scope, receive, send)

    # Слот гарантированно освобожден
    assert api.container.sse_slots.count(admin.id) == 0
