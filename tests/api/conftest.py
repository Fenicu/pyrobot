from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.app import create_api
from app.api.container import Container
from app.api.security import LoginRateLimiter
from app.config import AppConfig
from app.db.auth_repo import AuthRepo
from app.db.base import Database

PASSWORD = "correct horse battery"


def make_container(db: Database, *, secure: bool = False) -> Container:
    cfg = AppConfig(
        _env_file=None,
        transport="fake",
        cookie_secure=secure,
        admin_login="admin",
        admin_password=SecretStr(PASSWORD),
    )
    return Container(config=cfg, auth=AuthRepo(db), limiter=LoginRateLimiter())


@pytest.fixture
async def container(clean_db: Database) -> Container:
    c = make_container(clean_db)
    await c.auth.ensure_admin("admin", PASSWORD)
    return c


@pytest.fixture
async def api_client(container: Container) -> AsyncIterator[AsyncClient]:
    app = create_api(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def login(client: AsyncClient, password: str = PASSWORD) -> str:
    resp = await client.post("/api/v1/auth/login", json={"login": "admin", "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["csrf_token"])
