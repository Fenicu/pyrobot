from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from app.engine.scenarios.artifacts import artifact_start
from app.engine.scenarios.library import run_scenario
from app.engine.settings import ArtifactRunSection
from app.engine.state.model import CharacterState
from tests.engine.artifact_texts import (
    SCREEN,
    SCREEN_COLLECTING,
    START_BUTTONS,
    START_LIGHT,
    STARTED_LIGHT,
    game_text,
)
from tests.engine.fakegame import LIVE, Ref, World, running_world
from tests.engine.scenarios.certify import certifies
from tests.engine.scenarios.conftest import context

STARTING = LIVE.model_copy(
    update={
        "artifact_run": ArtifactRunSection(
            artifact="light", status="starting", requested_at=datetime.now(UTC)
        )
    }
)
PARAMS = {"artifact": "light"}


@pytest.fixture
async def starting() -> AsyncIterator[World]:
    async for w in running_world(STARTING):
        yield w


async def run(world: World) -> tuple[str, str, dict[str, object] | None]:
    ctx = context(world, scenario="artifact_start")
    result = await run_scenario(artifact_start, ctx, CharacterState(), PARAMS)
    return result.status, result.reason, result.details


def screens(world: World, *refs: Ref) -> None:
    """Ответы на /artefacts по очереди: последний — на все следующие."""
    for ref in refs:
        world.game.on_text("/artefacts", ref)


@certifies("artifact_start")
async def test_start_collect(starting: World) -> None:
    screens(starting, game_text(SCREEN))
    starting.game.on_text("/artr_light", game_text(START_LIGHT, buttons=START_BUTTONS))
    starting.game.on_click("artr_light_accept", new=(game_text(STARTED_LIGHT),))
    status, reason, details = await run(starting)
    assert (status, reason) == ("done", "started")
    assert details is not None and (details["artifact"], details["deed"]) == ("light", "walk")
    assert datetime.fromisoformat(str(details["started_at"])).tzinfo is not None
    assert starting.game.payloads() == ["/artefacts", "/artr_light", "artr_light_accept"]
    assert starting.gateway.lease is None
    # «Сбор начат!» прошёл через конвейер: уровень 0, 🔥 0.
    levels = starting.state.artifacts
    assert levels is not None and levels.value["light"] == 0


@certifies("artifact_start")
async def test_already_collecting_stops_on_screen(starting: World) -> None:
    screens(starting, game_text(SCREEN_COLLECTING))
    assert (await run(starting))[:2] == ("nothing", "already_collecting")
    assert starting.game.payloads() == ["/artefacts"]


@certifies("artifact_start")
async def test_not_recollectable_artifact(starting: World) -> None:
    screens(starting, game_text(SCREEN))
    ctx = context(starting, scenario="artifact_start")
    result = await run_scenario(artifact_start, ctx, CharacterState(), {"artifact": "book"})
    assert (result.status, result.reason) == ("nothing", "not_recollectable")
    assert starting.game.payloads() == ["/artefacts"]


@certifies("artifact_start")
@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (("refusals", 3604198), ("refused", "busy")),
        (("refusals", 3625754), ("failed", "wrong_screen")),
    ],
)
async def test_busy_or_wrong_screen_is_retried_later(
    starting: World, reply: tuple[str, int], expected: tuple[str, str]
) -> None:
    screens(starting, game_text(SCREEN))
    starting.game.on_text("/artr_light", reply)
    assert (await run(starting))[:2] == expected
    assert starting.game.payloads() == ["/artefacts", "/artr_light"]


@certifies("artifact_start")
async def test_unclear_click_checked_by_screen(starting: World) -> None:
    # «Сбор начат!» не пришёл (таймаут): экран артефактов показывает наш сбор.
    screens(starting, game_text(SCREEN), game_text(SCREEN_COLLECTING))
    starting.game.on_text("/artr_light", game_text(START_LIGHT, buttons=START_BUTTONS))
    assert (await run(starting))[:2] == ("done", "started_checked")
    assert starting.game.payloads() == [
        "/artefacts",
        "/artr_light",
        "artr_light_accept",
        "/artefacts",
    ]


@certifies("artifact_start")
async def test_gateway_refuses_click_without_starting_run(world: World) -> None:
    # Запись сбора не ждёт запуска (её отменили): шлюз клик не отправляет, экран сбора не видит.
    screens(world, game_text(SCREEN), game_text(SCREEN))
    world.game.on_text("/artr_light", game_text(START_LIGHT, buttons=START_BUTTONS))
    status, reason, _ = await run(world)
    assert (status, reason) == ("failed", "risky_requires_confirm")
    assert world.game.payloads() == ["/artefacts", "/artr_light", "/artefacts"]
