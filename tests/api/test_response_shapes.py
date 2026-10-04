"""Снимок формы ответов /engine/status, /tg/*, /state: модели ответов её не меняют."""

import re
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.api.routes_state import StateOut
from app.engine.bus import Bus
from app.engine.memory import MemoryJournal
from app.engine.parsing import default_parser
from app.engine.pipeline import Pipeline
from app.engine.settings import ChatsSection
from app.engine.state.reducer import StateReducer
from app.engine.transport.fake import FakeTgBackend
from tests.api.conftest import engines, login, run_engine
from tests.engine.test_facade import build
from tests.fixtures import game_msg

pytestmark = pytest.mark.db
ISO_UTC = re.compile(r"\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?\+00:00\Z")
TG_KEYS = ["state", "user_id", "attempt_id", "error", "bound_user_id", "app"]
BOUND = 267519921


class _Planner:
    current = "sleep"
    next_wake = datetime(2026, 9, 27, 18, 0, 5, 250000, tzinfo=UTC)


async def test_engine_status_shape(container: Container, api_client: AsyncClient) -> None:
    f = build(authorized=False, planner=_Planner())
    run_engine(container, f)
    await f.tg.boot()
    await login(api_client)
    body = (await api_client.get("/api/v1/accounts/1/engine/status")).json()
    assert list(body) == [
        "running",
        "status",
        "status_reason",
        "host_reason",
        "mode",
        "paused",
        "scenario",
        "next_wake",
        "killed",
        "kill_reason",
        "spending_blocked",
        "tg",
        "queue",
        "in_flight",
        "pipeline_backlog",
        "pipeline_healthy",
        "workers_ok",
        "lease_ok",
        "game_chat_member",
    ]
    assert body == {
        "running": True,
        "status": "enabled",
        "status_reason": None,
        "host_reason": None,
        "mode": "dry_run",
        "paused": False,
        "scenario": "sleep",
        # Прежний ответ (jsonable_encoder) отдавал даты через isoformat(), как `now` в /state.
        "next_wake": "2026-09-27T18:00:05.250000+00:00",
        "killed": False,
        "kill_reason": None,
        "spending_blocked": None,
        "tg": {
            "state": "unauthorized",
            "user_id": None,
            "attempt_id": None,
            "error": None,
            "bound_user_id": BOUND,
            "app": "server",
        },
        "queue": 0,
        "in_flight": None,
        "pipeline_backlog": 0,
        "pipeline_healthy": True,
        "workers_ok": True,
        "lease_ok": True,
        "game_chat_member": None,
    }


async def test_engine_status_without_wake(container: Container, api_client: AsyncClient) -> None:
    f = build(authorized=False)
    run_engine(container, f)
    await f.tg.boot()
    await login(api_client)
    body = (await api_client.get("/api/v1/accounts/1/engine/status")).json()
    assert (body["scenario"], body["next_wake"]) == (None, None)


async def test_tg_shapes(container: Container, api_client: AsyncClient) -> None:
    f = build(authorized=False, backend=FakeTgBackend(password="pw"))
    run_engine(container, f)
    await f.tg.boot()
    h = {"X-CSRF-Token": await login(api_client)}
    status = (await api_client.get("/api/v1/accounts/1/tg/status")).json()
    assert list(status) == TG_KEYS
    assert status == {
        "state": "unauthorized",
        "user_id": None,
        "attempt_id": None,
        "error": None,
        "bound_user_id": BOUND,
        "app": "server",
    }
    start = (
        await api_client.post(
            "/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+888"}
        )
    ).json()
    attempt = start["attempt_id"]
    assert list(start) == TG_KEYS and isinstance(attempt, str)
    assert start == {
        "state": "awaiting_code",
        "user_id": None,
        "attempt_id": attempt,
        "error": None,
        "bound_user_id": BOUND,
        "app": "server",
    }
    code = (
        await api_client.post(
            "/api/v1/accounts/1/tg/login/code",
            headers=h,
            json={"attempt_id": attempt, "code": "12345"},
        )
    ).json()
    assert list(code) == TG_KEYS and code["state"] == "awaiting_password"
    wrong = (
        await api_client.post(
            "/api/v1/accounts/1/tg/login/password",
            headers=h,
            json={"attempt_id": attempt, "password": "x"},
        )
    ).json()
    assert list(wrong) == TG_KEYS and wrong["error"] == "invalid_password"
    online = (
        await api_client.post(
            "/api/v1/accounts/1/tg/login/password",
            headers=h,
            json={"attempt_id": attempt, "password": "pw"},
        )
    ).json()
    assert online == {
        "state": "online",
        "user_id": 267519921,
        "attempt_id": None,
        "error": None,
        "bound_user_id": BOUND,
        "app": "server",
    }
    out = (await api_client.post("/api/v1/accounts/1/tg/logout", headers=h)).json()
    assert list(out) == TG_KEYS and out["state"] == "unauthorized"


def _with_state(container: Container) -> Pipeline:
    facade = build(authorized=False)
    facade.pipeline = Pipeline(
        journal=MemoryJournal(),
        parser=default_parser(ChatsSection()),
        reducer=StateReducer(),
        bus=Bus(),
    )
    run_engine(container, facade)
    return facade.pipeline


async def test_empty_state_shape(container: Container, api_client: AsyncClient) -> None:
    _with_state(container)
    await login(api_client)
    body = (await api_client.get("/api/v1/accounts/1/state")).json()
    assert list(body) == ["version", "now", "state", "stale"]
    assert (body["version"], body["state"], body["stale"]) == (0, {}, [])
    assert ISO_UTC.match(body["now"])
    StateOut.model_validate(body)


async def test_state_shape(container: Container, api_client: AsyncClient) -> None:
    pipeline = _with_state(container)
    await pipeline.process(replace(game_msg("profile", 3624478), date=datetime.now(UTC)))
    await pipeline.process(replace(game_msg("sleep", 3541942), date=datetime.now(UTC)))
    await login(api_client)
    body = (await api_client.get("/api/v1/accounts/1/state")).json()
    assert list(body) == ["version", "now", "state", "stale"]
    assert body["version"] == 2 and ISO_UTC.match(body["now"])
    # Состояние — снимок конвейера как есть: все поля (ненаблюдённые — null), тот же порядок,
    # без служебного `applied`.
    expected = {k: v for k, v in pipeline.state.items() if k != "applied"}
    assert list(body["state"]) == list(expected)
    assert body["state"] == expected
    assert body["state"]["money"]["at"] == pipeline.state["money"]["at"]
    assert isinstance(body["stale"], list)
    # Реальный снимок проходит публичную схему из OpenAPI.
    StateOut.model_validate(body)


async def test_state_sends_only_public_keys(container: Container, api_client: AsyncClient) -> None:
    # Снимок прошлой сборки может нести поле, которого уже нет в модели: наружу — только ключи
    # публичной схемы, значения — как в снимке.
    pipeline = _with_state(container)
    await pipeline.process(replace(game_msg("profile", 3624478), date=datetime.now(UTC)))
    f = engines(container).get(1).facade
    version, snapshot = f.state()
    legacy = {**snapshot, "legacy_field": {"value": 1, "at": "2026-09-27T12:00:00Z"}}
    f.state = lambda: (version, legacy)
    await login(api_client)
    body = (await api_client.get("/api/v1/accounts/1/state")).json()
    assert "legacy_field" not in body["state"] and "applied" not in body["state"]
    assert body["state"] == {k: v for k, v in snapshot.items() if k != "applied"}
    StateOut.model_validate(body)
