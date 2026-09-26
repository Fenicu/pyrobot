from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.engine.bus import Bus
from app.engine.memory import MemoryJournal
from app.engine.parsing import default_parser
from app.engine.pipeline import Pipeline
from app.engine.settings import ChatsSection
from app.engine.state.reducer import StateReducer
from tests.api.conftest import login
from tests.engine.test_facade import build
from tests.fixtures import game_msg

pytestmark = pytest.mark.db


@pytest.fixture
async def with_state(container: Container) -> Container:
    facade = build(authorized=False)
    facade.pipeline = Pipeline(
        journal=MemoryJournal(),
        parser=default_parser(ChatsSection()),
        reducer=StateReducer(),
        bus=Bus(),
    )
    container.facade = facade
    return container


async def _feed(container: Container, age: timedelta) -> None:
    assert container.facade is not None
    msg = replace(game_msg("profile", 3624478), date=datetime.now(UTC) - age)
    await container.facade.pipeline.process(msg)


async def test_state_requires_session(with_state: Container, api_client: AsyncClient) -> None:
    assert (await api_client.get("/api/v1/state")).status_code == 401


async def test_state_without_engine(container: Container, api_client: AsyncClient) -> None:
    await login(api_client)
    assert (await api_client.get("/api/v1/state")).status_code == 503


async def test_fresh_state(with_state: Container, api_client: AsyncClient) -> None:
    await login(api_client)
    await _feed(with_state, timedelta(0))
    body = (await api_client.get("/api/v1/state")).json()
    assert body["version"] == 1
    assert body["state"]["money"]["value"] == 867
    assert "money" not in body["stale"]
    assert "books" not in body["stale"]


async def test_stale_fields_listed(with_state: Container, api_client: AsyncClient) -> None:
    await login(api_client)
    await _feed(with_state, timedelta(hours=1))
    body = (await api_client.get("/api/v1/state")).json()
    assert {"money", "busy", "motivation"} <= set(body["stale"])
    assert "battle_at" not in body["stale"]
    assert "level" not in body["stale"]
