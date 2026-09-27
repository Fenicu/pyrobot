import time
from datetime import UTC, datetime, timedelta

import pytest

from app.engine.gametime import MSK, tasks_day
from app.engine.scenarios.context import ScenarioContext
from app.engine.scenarios.daily import daily_pick, daily_refresh
from app.engine.scenarios.library import run_scenario
from app.engine.state.model import CharacterState
from app.engine.types import IncomingMessage
from tests.engine.fakegame import GAME, World
from tests.engine.scenarios.certify import certifies
from tests.engine.scenarios.conftest import context

CREW = ("crew", 3625758)
OFFERS = ("daily", 3625760)
CHOSEN = ("daily", 3625786)
WRONG_SCREEN = ("refusals", 3625754)
TASK = "jobMoney_hard"


def open_screen(world: World, screen: tuple[str, int]) -> None:
    world.game.on_text("/crew", CREW)
    world.game.on_text("⏳Задания", screen)


@certifies("daily_refresh")
async def test_refresh_reads_screen_via_crew_menu(world: World) -> None:
    open_screen(world, CHOSEN)
    result = await run_scenario(daily_refresh, context(world), CharacterState(), {})
    assert (result.status, world.game.payloads()) == ("done", ["/crew", "⏳Задания"])
    personal = world.state.daily_personal
    assert personal is not None and personal.value.status == "active"
    assert world.gateway.lease is None


@certifies("daily_refresh")
async def test_refresh_off_screen_is_wrong_screen(world: World) -> None:
    open_screen(world, WRONG_SCREEN)
    result = await run_scenario(daily_refresh, context(world), CharacterState(), {})
    assert (result.status, result.reason) == ("failed", "wrong_screen")
    assert world.gateway.lease is None


@certifies("daily_refresh")
async def test_refresh_without_answer_fails(world: World) -> None:
    world.game.on_text("/crew", CREW)
    result = await run_scenario(daily_refresh, context(world), CharacterState(), {})
    assert (result.status, result.reason) == ("failed", "timeout")
    assert world.game.payloads() == ["/crew", "⏳Задания"]


@certifies("daily_refresh")
async def test_refresh_is_nav_even_in_simulation(world: World) -> None:
    open_screen(world, OFFERS)
    ctx = context(world, simulate=True)
    result = await run_scenario(daily_refresh, ctx, CharacterState(), {})
    assert result.status == "done"


@certifies("daily_pick")
async def test_pick_confirms_offer(world: World) -> None:
    open_screen(world, OFFERS)
    world.game.on_text(f"/t_{TASK}", ("daily", 3625762))
    world.game.on_click(f"t_{TASK}_confirm", edit=("daily", 3625782, 1))
    result = await run_scenario(daily_pick, context(world), CharacterState(), {"task": TASK})
    assert (result.status, result.reason) == ("done", "task_chosen")
    assert world.game.payloads() == ["/crew", "⏳Задания", f"/t_{TASK}", f"t_{TASK}_confirm"]
    # Клик — по сообщению подтверждения, его и правит игра.
    clicked = world.game.messages[world.game.sent[-1].message_id or 0]
    assert clicked.kind == "edit" and (clicked.text or "").startswith("⏳Выбранное задание")
    personal = world.state.daily_personal
    assert personal is not None
    assert personal.value.status == "active"
    assert personal.value.chosen is not None and personal.value.chosen.type == "jobMoney"
    assert world.gateway.lease is None


@certifies("daily_pick")
@pytest.mark.parametrize(
    ("screen", "task", "reason"),
    [
        (CHOSEN, TASK, "already_chosen"),
        (("daily", 3348708), TASK, "already_chosen"),
        (OFFERS, "convDets_hard", "offer_gone"),
    ],
)
async def test_pick_reads_screen_first(
    world: World, screen: tuple[str, int], task: str, reason: str
) -> None:
    open_screen(world, screen)
    result = await run_scenario(daily_pick, context(world), CharacterState(), {"task": task})
    assert (result.status, result.reason) == ("nothing", reason)
    assert world.game.payloads() == ["/crew", "⏳Задания"]
    assert world.gateway.lease is None


@certifies("daily_pick")
async def test_pick_off_screen_is_wrong_screen(world: World) -> None:
    open_screen(world, WRONG_SCREEN)
    result = await run_scenario(daily_pick, context(world), CharacterState(), {"task": TASK})
    assert (result.status, result.reason) == ("failed", "wrong_screen")
    assert world.game.payloads() == ["/crew", "⏳Задания"]


@certifies("daily_pick")
async def test_pick_command_off_screen_is_wrong_screen(world: World) -> None:
    # Игрок с телефона ушёл с экрана заданий между шагами: /t_… отвечает общей справкой.
    open_screen(world, OFFERS)
    world.game.on_text(f"/t_{TASK}", ("refusals", 3625756))
    result = await run_scenario(daily_pick, context(world), CharacterState(), {"task": TASK})
    assert (result.status, result.reason) == ("failed", "wrong_screen")
    assert world.game.payloads() == ["/crew", "⏳Задания", f"/t_{TASK}"]


@certifies("daily_pick")
async def test_pick_without_edit_is_failed(world: World) -> None:
    open_screen(world, OFFERS)
    world.game.on_text(f"/t_{TASK}", ("daily", 3625762))
    result = await run_scenario(daily_pick, context(world), CharacterState(), {"task": TASK})
    assert (result.status, result.reason) == ("failed", "timeout")
    assert world.game.payloads()[-1] == f"t_{TASK}_confirm"


@certifies("daily_pick")
async def test_pick_in_dry_run_reads_screen_but_does_not_choose(world: World) -> None:
    open_screen(world, OFFERS)
    world.game.on_text(f"/t_{TASK}", ("daily", 3625762))
    ctx = ScenarioContext(
        world.gateway, game_chat_id=GAME, simulate=False, paused=lambda: False, dry_run=True
    )
    result = await run_scenario(daily_pick, ctx, CharacterState(), {"task": TASK})
    assert (result.status, result.reason) == ("suppressed", "dry_run")
    assert world.game.payloads() == ["/crew", "⏳Задания"]


class ShiftClock:
    """Часы впереди настоящих (шлюз принимает ответы не раньше момента отправки): за 30 с до
    полуночи MSK через сутки."""

    def __init__(self) -> None:
        real = datetime.now(UTC)
        day = tasks_day(real) + timedelta(days=2)
        midnight = datetime(day.year, day.month, day.day, tzinfo=MSK)
        target = midnight - timedelta(seconds=30)
        self.shift = target - real

    def now(self) -> datetime:
        return datetime.now(UTC) + self.shift

    def monotonic(self) -> float:
        return time.monotonic()


def cross_midnight_after(world: World, clock: ShiftClock, prefix: str) -> None:
    push = world.game._push

    async def pushed(msg: IncomingMessage) -> None:
        await push(msg)
        if (msg.text or "").startswith(prefix):
            clock.shift += timedelta(minutes=1)

    world.game._push = pushed  # type: ignore[method-assign]


def clocked(world: World, clock: ShiftClock) -> ScenarioContext:
    world.game.clock = clock
    return ScenarioContext(
        world.gateway, game_chat_id=GAME, simulate=False, paused=lambda: False, clock=clock
    )


@certifies("daily_pick")
async def test_pick_before_midnight_still_chooses(world: World) -> None:
    clock = ShiftClock()
    open_screen(world, OFFERS)
    world.game.on_text(f"/t_{TASK}", ("daily", 3625762))
    world.game.on_click(f"t_{TASK}_confirm", edit=("daily", 3625782, 1))
    ctx = clocked(world, clock)
    result = await run_scenario(daily_pick, ctx, CharacterState(), {"task": TASK})
    assert (result.status, result.reason) == ("done", "task_chosen")


@certifies("daily_pick")
@pytest.mark.parametrize(
    ("prefix", "sent"),
    [
        # Экран — вчерашний: /t_… не уходит.
        ("⏳Ежедневные задания", ["/crew", "⏳Задания"]),
        # Подтверждение — вчерашнее: кнопку не жмём.
        ("Ты собираешься выбрать задание", ["/crew", "⏳Задания", f"/t_{TASK}"]),
    ],
)
async def test_pick_stops_when_day_changes_between_steps(
    world: World, prefix: str, sent: list[str]
) -> None:
    clock = ShiftClock()
    open_screen(world, OFFERS)
    world.game.on_text(f"/t_{TASK}", ("daily", 3625762))
    world.game.on_click(f"t_{TASK}_confirm", edit=("daily", 3625782, 1))
    cross_midnight_after(world, clock, prefix)
    ctx = clocked(world, clock)
    result = await run_scenario(daily_pick, ctx, CharacterState(), {"task": TASK})
    assert (result.status, result.reason) == ("nothing", "day_changed")
    assert world.game.payloads() == sent
    assert world.gateway.lease is None
