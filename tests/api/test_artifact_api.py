from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.engine.facade import EngineFacade
from app.engine.settings import ArtifactRunSection, EngineSection, Settings, StaticSettings
from app.engine.state.model import ArtifactCollect, CharacterState, Obs, dump_state
from tests.api.conftest import A1, login, run_engine
from tests.engine.test_facade import build

pytestmark = pytest.mark.db
LIVE = Settings(engine=EngineSection(mode="live"))
ART = f"{A1}/artifact"


def snapshot(**fields: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    state = CharacterState(**{k: Obs(value=v, at=now) for k, v in fields.items()})
    return dump_state(state)


async def engine(
    container: Container,
    *,
    settings: Settings = LIVE,
    state: dict[str, Any] | None = None,
    online: bool = True,
) -> EngineFacade:
    f = build(settings=StaticSettings(settings.model_copy(deep=True)), snapshot=state)
    await f.pipeline.load()
    run_engine(container, f)
    if online:
        await f.tg.boot()
    return f


async def headers(client: AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": await login(client)}


async def test_view_without_engine_from_database(
    container: Container, api_client: AsyncClient
) -> None:
    await login(api_client)
    body = (await api_client.get(ART)).json()
    assert body["run"]["status"] == "idle" and body["run"]["lottery_switched"] is False
    assert (body["levels"], body["collecting"], body["level"]) == ({}, None, None)
    assert body["tactic"] == {"book": ["learn"], "fax": ["job"], "light": ["walk"]}
    assert (body["lottery_on"], body["lottery_on_start"]) == (True, True)
    assert body["pace"] == {"levels_per_day": None, "forecast_level": None}
    h = await headers(api_client)
    blocked = await api_client.post(f"{ART}/start", headers=h, json={"artifact": "light"})
    assert (blocked.status_code, blocked.json()) == (503, {"detail": "engine not running"})


async def test_start_snapshots_lottery_and_cancel_returns_it(
    container: Container, api_client: AsyncClient
) -> None:
    off = LIVE.model_copy(update={"features": LIVE.features.model_copy(update={"lottery": False})})
    f = await engine(container, settings=off, state=snapshot(artifacts={"light": 84}))
    h = await headers(api_client)
    body = {"artifact": "light", "lottery_max": True}
    started = await api_client.post(f"{ART}/start", headers=h, json=body)
    assert started.status_code == 202
    out = started.json()
    assert (out["run"]["status"], out["run"]["artifact"], out["run"]["lottery_switched"]) == (
        "starting",
        "light",
        True,
    )
    assert f.settings.current.features.lottery
    again = await api_client.post(f"{ART}/start", headers=h, json=body)
    assert (again.status_code, again.json()) == (409, {"detail": "run_in_progress"})
    paused = await api_client.post(f"{ART}/pause", headers=h)
    assert (paused.status_code, paused.json()) == (409, {"detail": "no_run"})
    cancelled = await api_client.post(f"{ART}/cancel", headers=h)
    assert cancelled.status_code == 200 and cancelled.json()["run"]["status"] == "idle"
    assert not f.settings.current.features.lottery


@pytest.mark.parametrize(
    ("settings", "state", "online", "code"),
    [
        (Settings(), None, True, "dry_run"),
        (LIVE, None, False, "tg_not_online"),
        (LIVE, {"artifacts": {"light": 100}}, True, "artifact_max"),
        (
            LIVE,
            {
                "artifact_collect": ArtifactCollect(
                    artifact="fax", ends_at=datetime.now(UTC) + timedelta(days=2)
                )
            },
            True,
            "locked_until",
        ),
    ],
)
async def test_start_conflicts(
    container: Container,
    api_client: AsyncClient,
    settings: Settings,
    state: dict[str, Any] | None,
    online: bool,
    code: str,
) -> None:
    await engine(
        container,
        settings=settings,
        state=snapshot(**state) if state else None,
        online=online,
    )
    h = await headers(api_client)
    r = await api_client.post(f"{ART}/start", headers=h, json={"artifact": "light"})
    assert (r.status_code, r.json()) == (409, {"detail": code})


async def test_pause_resume_cancel_keep_lock(
    container: Container, api_client: AsyncClient
) -> None:
    now = datetime.now(UTC)
    run = ArtifactRunSection.model_validate(
        {
            "artifact": "fax",
            "status": "active",
            "started_at": now - timedelta(days=2),
            "ends_at": now + timedelta(days=8),
        }
    )
    f = await engine(
        container,
        settings=LIVE.model_copy(update={"artifact_run": run}),
        state=snapshot(artifacts={"fax": 30}),
    )
    h = await headers(api_client)
    view = (await api_client.get(ART)).json()
    assert view["level"] == 30 and view["pace"]["levels_per_day"] == 15.0
    assert (await api_client.post(f"{ART}/pause", headers=h)).json()["run"]["status"] == "paused"
    assert (await api_client.post(f"{ART}/resume", headers=h)).json()["run"]["status"] == "active"
    done = (await api_client.post(f"{ART}/cancel", headers=h)).json()
    assert (done["run"]["status"], done["run"]["result_level"], done["level"]) == (
        "cancelled",
        30,
        30,
    )
    assert done["next_start_at"] == done["run"]["ends_at"]
    locked = await api_client.post(f"{ART}/start", headers=h, json={"artifact": "light"})
    assert locked.json() == {"detail": "locked_until"}
    assert f.settings.current.artifact_run.status == "cancelled"


async def test_adopt_external_collect(container: Container, api_client: AsyncClient) -> None:
    ends = datetime.now(UTC) + timedelta(days=3)
    await engine(
        container, state=snapshot(artifact_collect=ArtifactCollect(artifact="fax", ends_at=ends))
    )
    h = await headers(api_client)
    view = (await api_client.get(ART)).json()
    assert view["external"] is True and view["collecting"]["artifact"] == "fax"
    adopted = (await api_client.post(f"{ART}/adopt", headers=h)).json()
    assert (adopted["run"]["status"], adopted["run"]["artifact"], adopted["external"]) == (
        "active",
        "fax",
        False,
    )
    again = await api_client.post(f"{ART}/adopt", headers=h)
    assert (again.status_code, again.json()) == (409, {"detail": "run_in_progress"})


async def test_unknown_artifact_is_422(container: Container, api_client: AsyncClient) -> None:
    await engine(container)
    h = await headers(api_client)
    r = await api_client.post(f"{ART}/start", headers=h, json={"artifact": "box"})
    assert r.status_code == 422


async def test_start_with_deeds_off_is_409(container: Container, api_client: AsyncClient) -> None:
    off = LIVE.model_copy(update={"features": LIVE.features.model_copy(update={"deeds": False})})
    f = await engine(container, settings=off)
    h = await headers(api_client)
    r = await api_client.post(f"{ART}/start", headers=h, json={"artifact": "light"})
    assert (r.status_code, r.json()) == (409, {"detail": "deeds_disabled"})
    assert f.settings.current.artifact_run.status == "idle"
