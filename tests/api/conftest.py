import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.app import create_api
from app.api.container import Container
from app.api.security import LoginRateLimiter
from app.config import AppConfig
from app.db.accounts import AccountRepo
from app.db.auth_repo import AuthRepo
from app.db.base import Database
from app.engine.facade import EngineFacade
from app.engine.stream import EventStream

PASSWORD = "correct horse battery"
# Пути аккаунта 1 — аккаунта учётки admin в тестах.
A1 = "/api/v1/accounts/1"


class FakeEngine:
    """Движок аккаунта для тестов API: фасад на памяти и поток событий движка."""

    def __init__(self, facade: EngineFacade, account_id: int) -> None:
        self.account_id = account_id
        self.facade = facade
        self.stream = facade.stream if facade.stream is not None else EventStream()


class FakeEngines:
    """Реестр движков: тест кладёт движок руками (`put`). `starting` — движки, которые
    регистрируются, как только их ждут; иначе `wait_registered` ждёт весь срок."""

    def __init__(self) -> None:
        self.engines: dict[int, FakeEngine] = {}
        self.starting: dict[int, FakeEngine] = {}
        self.reasons: dict[int, str] = {}
        self.waited: list[tuple[int, float]] = []

    def put(self, facade: EngineFacade, account_id: int = 1) -> FakeEngine:
        engine = FakeEngine(facade, account_id)
        self.engines[account_id] = engine
        return engine

    def get(self, account_id: int) -> Any:
        return self.engines.get(account_id)

    async def wait_registered(self, account_id: int, timeout_s: float) -> Any:
        self.waited.append((account_id, timeout_s))
        engine = self.starting.pop(account_id, None)
        if engine is not None:
            self.engines[account_id] = engine
            return engine
        await asyncio.sleep(timeout_s)
        return self.engines.get(account_id)

    def host_reason(self, account_id: int) -> str | None:
        return self.reasons.get(account_id)


def engines(c: Container) -> FakeEngines:
    return cast(FakeEngines, c.engines)


def run_engine(c: Container, facade: EngineFacade, account_id: int = 1) -> FakeEngine:
    """Движок аккаунта запущен: пути аккаунта идут в `facade`."""
    return engines(c).put(facade, account_id)


def make_container(db: Database, *, secure: bool = False) -> Container:
    cfg = AppConfig(
        _env_file=None,
        transport="fake",
        cookie_secure=secure,
        admin_login="admin",
        admin_password=SecretStr(PASSWORD),
    )
    return Container(
        config=cfg,
        auth=AuthRepo(db),
        limiter=LoginRateLimiter(),
        db=db,
        accounts=AccountRepo(db),
        engines=FakeEngines(),
    )


@pytest.fixture
async def container(clean_db: Database) -> Container:
    c = make_container(clean_db)
    await c.auth.ensure_admin("admin", PASSWORD)
    # Аккаунт 1 — учётки admin.
    await c.accounts.adopt_orphans()
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


@dataclass
class Api:
    """Вошедший admin: клиент, заголовок CSRF, контейнер и реестр движков."""

    client: AsyncClient
    headers: dict[str, str]
    container: Container
    engines: FakeEngines
    db: Database


@pytest.fixture
async def api(container: Container, api_client: AsyncClient, clean_db: Database) -> Api:
    csrf = await login(api_client)
    return Api(api_client, {"X-CSRF-Token": csrf}, container, engines(container), clean_db)
