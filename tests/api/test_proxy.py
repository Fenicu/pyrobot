import pytest
from httpx import ASGITransport, AsyncClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.app import create_api
from app.api.container import Container
from app.api.security import LoginRateLimiter

pytestmark = pytest.mark.db
CADDY = "10.10.40.3"


def _client(container: Container, peer: str) -> AsyncClient:
    # Так uvicorn оборачивает приложение при --proxy-headers --forwarded-allow-ips.
    app = ProxyHeadersMiddleware(create_api(container), trusted_hosts=CADDY)
    return AsyncClient(transport=ASGITransport(app=app, client=(peer, 4000)), base_url="http://t")


async def _fail(client: AsyncClient, forwarded: str) -> int:
    body = {"login": "admin", "password": "wrong password"}
    r = await client.post("/api/v1/auth/login", json=body, headers={"X-Forwarded-For": forwarded})
    return r.status_code


async def test_login_limiter_keys_by_forwarded_client(container: Container) -> None:
    container.limiter = LoginRateLimiter(free_attempts=0)
    async with _client(container, CADDY) as client:
        assert [await _fail(client, "1.1.1.1") for _ in range(2)] == [401, 429]
        # Другой клиент за тем же Caddy — свой счётчик.
        assert await _fail(client, "2.2.2.2") == 401


async def test_forwarded_header_ignored_from_untrusted_peer(container: Container) -> None:
    container.limiter = LoginRateLimiter(free_attempts=0)
    async with _client(container, "10.10.40.99") as client:
        assert await _fail(client, "1.1.1.1") == 401
        # Подмена заголовка не даёт обойти лимит: ключ — адрес самого соединения.
        assert await _fail(client, "3.3.3.3") == 429
