"""Команду, которую игра принимает только со своего экрана, сценарий шлёт сразу после экрана:
безопасной точки между ними нет. Пауза (её проверяет безопасная точка) пару не разрывает."""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.engine.scenarios.context import ScenarioContext
from app.engine.scenarios.library import fastfood, free_item, levelup, run_scenario
from app.engine.scenarios.metro import metro
from app.engine.scenarios.obligations import battle_target, smoothie, stocks_dump
from app.engine.state.model import CharacterState, Obs, Skills
from tests.engine.fakegame import GAME, Ref, World
from tests.engine.metro.simgame import enter_with_real_frames
from tests.engine.scenarios.certify import certifies
from tests.engine.scenarios.conftest import context

SKILLS = CharacterState(
    skills=Obs(
        value=Skills(practice=461, theory=462, cunning=344, wisdom=345), at=datetime.now(UTC)
    )
)
PAIRS: dict[str, tuple[Any, CharacterState, dict[str, Any], list[tuple[str, Ref]]]] = {
    "prizebox": (
        free_item,
        CharacterState(),
        {"item": "prizebox"},
        [("/inv", ("items", 3625715)), ("/unbox", ("items", 3625717))],
    ),
    "container_small": (
        free_item,
        CharacterState(),
        {"item": "container_small"},
        [("/gifts", ("items", 3623585)), ("/unbox_ls", ("items", 3517971))],
    ),
    "container_medium": (
        free_item,
        CharacterState(),
        {"item": "container_medium"},
        [("/gifts", ("items", 3611231)), ("/unbox_lm", ("items", 3611233))],
    ),
    "fastfood": (
        fastfood,
        CharacterState(),
        {"food": "hotdog"},
        [("/to_eat", ("food", 3521844)), ("🌭Хот-дог", ("food", 3624983))],
    ),
    "levelup": (
        levelup,
        SKILLS,
        {},
        [
            ("/levelup", ("levelup", 3532816)),
            ("+1 🔨Практика", ("levelup", 3532818)),
            ("+1 🐿Хитрость", ("levelup", 3532820)),
        ],
    ),
    "battle_target": (
        battle_target,
        CharacterState(),
        {"target": "📯Pied Piper"},
        [("⚔Битва", ("screens", 3613862)), ("📯Pied Piper", ("battle", 3624402))],
    ),
    "stocks_dump": (
        stocks_dump,
        CharacterState(),
        {"keep": 150, "margin": 5},
        [("/stock", ("stocks", 3624065)), ("/buys_stark_69", ("stocks", 3625255))],
    ),
}


@certifies(*PAIRS)
@pytest.mark.parametrize("name", sorted(PAIRS))
async def test_pause_does_not_split_screen_and_its_command(world: World, name: str) -> None:
    fn, state, params, chain = PAIRS[name]
    for text, ref in chain:
        world.game.on_text(text, ref)
    result = await run_scenario(fn, context(world, paused=True), state, params)
    assert result.status == "done"
    assert world.game.payloads() == [text for text, _ in chain]
    assert world.gateway.lease is None


@certifies("smoothie")
async def test_cook_button_follows_smoothie_screen(world: World) -> None:
    # 🍹Готовить — кнопка экрана Смузийной: сразу после него; пауза — на точке перед фруктом.
    world.game.on_text("/smoothie", ("smoothie", 3581573))
    world.game.on_text("🍹Готовить", ("smoothie_cooking", 3625241, 0))
    ctx = context(world, paused=True)
    result = await run_scenario(smoothie, ctx, CharacterState(), {"recipe": "🍇🥕🥕🍋🍅"})
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == ["/smoothie", "🍹Готовить"]


@certifies("metro")
async def test_metro_button_follows_office_menu(world: World) -> None:
    # 🚇Метро — кнопка меню офиса: сразу после него; пауза — на точке перед «Вхожу».
    enter_with_real_frames(world.game)
    ctx = ScenarioContext(
        world.gateway, game_chat_id=GAME, simulate=False, paused=lambda: True, timeout_s=0.3
    )
    result = await run_scenario(metro, ctx, CharacterState(), {})
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == ["🏢Офис", "🚇Метро"]
