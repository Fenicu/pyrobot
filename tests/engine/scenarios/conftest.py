import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.engine.gametime import MSK, tasks_day
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


class ShiftClock:
    """Часы впереди настоящих (шлюз принимает ответы не раньше момента отправки): через двое суток
    в `offset` от полуночи MSK. По умолчанию — полдень: день заданий не сменится посреди теста,
    когда бы его ни запустили."""

    def __init__(self, offset: timedelta = timedelta(hours=12)) -> None:
        real = datetime.now(UTC)
        day = tasks_day(real) + timedelta(days=2)
        target = datetime(day.year, day.month, day.day, tzinfo=MSK) + offset
        self.shift = target - real

    def now(self) -> datetime:
        return datetime.now(UTC) + self.shift

    def monotonic(self) -> float:
        return time.monotonic()
