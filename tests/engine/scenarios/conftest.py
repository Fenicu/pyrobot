from collections.abc import AsyncIterator

import pytest

from app.engine.notify import NotifierPort
from app.engine.scenarios.context import ScenarioContext
from tests.engine.fakegame import GAME, World, running_world


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async for w in running_world():
        yield w


def context(
    world: World,
    *,
    simulate: bool = False,
    paused: bool = False,
    notifier: NotifierPort | None = None,
) -> ScenarioContext:
    return ScenarioContext(
        world.gateway,
        game_chat_id=GAME,
        simulate=simulate,
        paused=lambda: paused,
        timeout_s=0.3,
        notifier=notifier,
    )
