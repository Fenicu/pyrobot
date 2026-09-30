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
from tests.api.conftest import engines, login, run_engine
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
    run_engine(container, facade)
    return container


async def _feed(container: Container, age: timedelta) -> None:
    engine = engines(container).get(1)
    msg = replace(game_msg("profile", 3624478), date=datetime.now(UTC) - age)
    await engine.facade.pipeline.process(msg)


async def test_state_requires_session(with_state: Container, api_client: AsyncClient) -> None:
    assert (await api_client.get("/api/v1/accounts/1/state")).status_code == 401


async def test_state_without_engine(container: Container, api_client: AsyncClient) -> None:
    # Без движка — снимок из базы; до первого сохранения он пуст.
    await login(api_client)
    body = (await api_client.get("/api/v1/accounts/1/state")).json()
    assert (body["version"], body["state"], body["stale"]) == (0, {}, [])


async def test_fresh_state(with_state: Container, api_client: AsyncClient) -> None:
    await login(api_client)
    await _feed(with_state, timedelta(0))
    body = (await api_client.get("/api/v1/accounts/1/state")).json()
    assert body["version"] == 1
    assert body["state"]["money"]["value"] == 867
    assert "money" not in body["stale"]
    assert "books" not in body["stale"]
    assert body["state"]["books"] is None
    assert "applied" not in body["state"]


async def test_stale_fields_listed(with_state: Container, api_client: AsyncClient) -> None:
    await login(api_client)
    await _feed(with_state, timedelta(hours=1))
    body = (await api_client.get("/api/v1/accounts/1/state")).json()
    assert {"money", "busy", "motivation"} <= set(body["stale"])
    assert "battle_at" not in body["stale"]
    assert "level" not in body["stale"]
