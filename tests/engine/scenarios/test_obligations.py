import re
from dataclasses import replace
from datetime import date

import pytest

from app.engine.gametime import tasks_day
from app.engine.scenarios.library import run_scenario
from app.engine.scenarios.obligations import (
    battle_target,
    bulls_join,
    factory_report,
    factory_signup,
    smoothie,
    stocks_dump,
    tangerine,
)
from app.engine.state.model import CharacterState
from app.engine.types import IncomingMessage
from tests.engine.fakegame import World
from tests.engine.scenarios.certify import certifies
from tests.engine.scenarios.conftest import ShiftClock, context
from tests.fixtures import game_msg

TANGERINE_CHAT, REPLY_TO = -1001377961602, 927136
CODE = "join_fight_AaBH89kYd2J"


@certifies("battle_target")
@pytest.mark.parametrize(("target", "fixture"), [("📯Pied Piper", 3624402), ("🛡Защита", 3569475)])
async def test_battle_target_from_menu(world: World, target: str, fixture: int) -> None:
    world.game.on_text("⚔Битва", ("screens", 3613862))
    world.game.on_text(target, ("battle", fixture))
    result = await run_scenario(
        battle_target, context(world), CharacterState(), {"target": target}
    )
    assert result.status == "done"
    assert world.game.payloads() == ["⚔Битва", target]
    assert world.gateway.lease is None


@certifies("battle_target")
async def test_battle_target_without_menu_stops(world: World) -> None:
    result = await run_scenario(
        battle_target, context(world), CharacterState(), {"target": "📯Pied Piper"}
    )
    assert (result.status, result.reason, world.game.payloads()) == (
        "stopped",
        "timeout",
        ["⚔Битва"],
    )


@certifies("stocks_dump")
async def test_stocks_dump_buys_best_foreign_stock(world: World) -> None:
    world.game.on_text("/stock", ("stocks", 3624065))
    world.game.on_text("/buys_stark_69", ("stocks", 3625255))
    params = {"keep": 150, "margin": 5}
    result = await run_scenario(stocks_dump, context(world), CharacterState(), params)
    assert result.status == "done"
    assert world.game.payloads() == ["/stock", "/buys_stark_69"]


@certifies("stocks_dump")
@pytest.mark.parametrize(
    ("fixture", "keep", "reason"),
    [(3624609, 150, "market_closed"), (3624065, 5000, "not_enough_money")],
)
async def test_stocks_dump_nothing(world: World, fixture: int, keep: int, reason: str) -> None:
    world.game.on_text("/stock", ("stocks", fixture))
    params = {"keep": keep, "margin": 5}
    result = await run_scenario(stocks_dump, context(world), CharacterState(), params)
    assert (result.status, result.reason, world.game.payloads()) == ("nothing", reason, ["/stock"])


@certifies("stocks_dump")
async def test_stocks_dump_no_candidate_within_limits(world: World) -> None:
    world.game.on_text("/stock", ("stocks", 3624065))
    params = {"keep": 150, "margin": 60}
    result = await run_scenario(stocks_dump, context(world), CharacterState(), params)
    assert (result.status, result.reason) == ("nothing", "no_stock")


CREW_MENU = ("crew", 3624389)


def factory_chain(world: World, screen: int = 3624391) -> None:
    world.game.on_text("/crew", CREW_MENU)
    world.game.on_text("/crew_factory", ("crew", screen))


@certifies("factory_signup")
async def test_factory_signup_via_crew_menu(world: World) -> None:
    # Живая цепочка 27.09 18:00: меню команды → экран фабрики → запись.
    world.game.on_text("/crew", ("crew", 3626163))
    world.game.on_text("/crew_factory", ("crew", 3626165))
    world.game.on_text("👍Записаться", ("crew", 3626167))
    result = await run_scenario(factory_signup, context(world), CharacterState(), {})
    assert (result.status, result.reason) == ("done", "signed")
    assert world.game.payloads() == ["/crew", "/crew_factory", "👍Записаться"]
    signed = world.state.factory_signed
    assert signed is not None and signed.value is True
    assert world.gateway.lease is None


@certifies("factory_signup")
@pytest.mark.parametrize(("reply", "reason"), [(3624393, "signed"), (3621811, "skip")])
async def test_factory_signup(world: World, reply: int, reason: str) -> None:
    factory_chain(world)
    world.game.on_text("👍Записаться", ("crew", reply))
    result = await run_scenario(factory_signup, context(world), CharacterState(), {})
    assert (result.status, result.reason) == ("done", reason)
    assert world.game.payloads() == ["/crew", "/crew_factory", "👍Записаться"]


@certifies("factory_signup")
@pytest.mark.parametrize(("screen", "reason"), [(3572473, "signed"), (3586815, "closed")])
async def test_factory_nothing_to_do(world: World, screen: int, reason: str) -> None:
    factory_chain(world, screen)
    result = await run_scenario(factory_signup, context(world), CharacterState(), {})
    assert (result.status, result.reason, world.game.payloads()) == (
        "nothing",
        reason,
        ["/crew", "/crew_factory"],
    )


@certifies("factory_signup")
async def test_factory_signup_busy(world: World) -> None:
    factory_chain(world)
    world.game.on_text("👍Записаться", ("refusals", 3517360))
    result = await run_scenario(factory_signup, context(world), CharacterState(), {})
    assert (result.status, result.reason) == ("refused", "busy")


@certifies("factory_signup")
async def test_factory_off_screen_is_wrong_screen(world: World) -> None:
    # 27.09 18:00: /crew_factory не из меню команды — общая справка «Если жаждешь общения…».
    world.game.on_text("/crew", CREW_MENU)
    world.game.on_text("/crew_factory", ("refusals", 3626159))
    result = await run_scenario(factory_signup, context(world), CharacterState(), {})
    assert (result.status, result.reason) == ("failed", "wrong_screen")
    assert world.game.payloads() == ["/crew", "/crew_factory"]
    assert world.gateway.lease is None


@certifies("factory_signup")
async def test_signup_button_off_screen_is_wrong_screen(world: World) -> None:
    # Экран фабрики пришёл, а 👍Записаться игра приняла уже не с него — общая справка.
    factory_chain(world)
    world.game.on_text("👍Записаться", ("refusals", 3626159))
    result = await run_scenario(factory_signup, context(world), CharacterState(), {})
    assert (result.status, result.reason) == ("failed", "wrong_screen")
    assert world.game.payloads() == ["/crew", "/crew_factory", "👍Записаться"]


@certifies("factory_signup")
async def test_factory_without_crew_menu_fails(world: World) -> None:
    result = await run_scenario(factory_signup, context(world), CharacterState(), {})
    assert (result.status, result.reason) == ("failed", "timeout")
    assert world.game.payloads() == ["/crew"]


@certifies("bulls_join")
async def test_bulls_join(world: World) -> None:
    world.game.on_text(CODE, ("bulls", 3624430))
    result = await run_scenario(bulls_join, context(world), CharacterState(), {"code": CODE})
    assert result.status == "done"
    assert world.game.payloads() == [CODE]


@certifies("bulls_join")
@pytest.mark.parametrize(
    ("fixture", "reason"),
    [(3610979, "already_won"), (3526549, "ended"), (3533483, "missing")],
)
async def test_bulls_join_refused(world: World, fixture: int, reason: str) -> None:
    world.game.on_text(CODE, ("bulls", fixture))
    result = await run_scenario(bulls_join, context(world), CharacterState(), {"code": CODE})
    assert (result.status, result.reason) == ("refused", reason)


@certifies("tangerine")
async def test_tangerine_silence_means_sent(world: World) -> None:
    params = {"chat": TANGERINE_CHAT, "reply_to": REPLY_TO}
    result = await run_scenario(tangerine, context(world), CharacterState(), params)
    assert (result.status, result.reason) == ("done", "no_error")
    [sent] = world.game.sent
    assert (sent.payload, sent.chat_id, sent.reply_to) == ("/gt", TANGERINE_CHAT, REPLY_TO)
    assert world.gateway.spending_blocked is None


@certifies("tangerine")
async def test_tangerine_ignores_unrelated_refusal(world: World) -> None:
    world.game.on_text("/gt", ("refusals", 3517857))
    params = {"chat": TANGERINE_CHAT, "reply_to": REPLY_TO}
    result = await run_scenario(tangerine, context(world), CharacterState(), params)
    assert (result.status, result.reason) == ("done", "no_error")
    assert world.gateway.spending_blocked is None


@certifies("tangerine")
@pytest.mark.parametrize(("fixture", "reason"), [(3599304, "not_player"), (3616906, "cooldown")])
async def test_tangerine_refused_in_game_chat(world: World, fixture: int, reason: str) -> None:
    world.game.on_text("/gt", ("tangerine", fixture))
    params = {"chat": TANGERINE_CHAT, "reply_to": REPLY_TO}
    result = await run_scenario(tangerine, context(world), CharacterState(), params)
    assert (result.status, result.reason) == ("refused", reason)


@certifies("smoothie")
@pytest.mark.parametrize(
    ("screen", "recipe", "reason"),
    [(3523569, "🍇🥕🥕🍋🍅", "cooked_today"), (3581573, "🍋🍋🍋🍋🍋", "no_ingredients")],
)
async def test_smoothie_checks_screen_first(
    world: World, screen: int, recipe: str, reason: str
) -> None:
    world.game.on_text("/smoothie", ("smoothie", screen))
    result = await run_scenario(smoothie, context(world), CharacterState(), {"recipe": recipe})
    assert (result.status, result.reason, world.game.payloads()) == (
        "nothing",
        reason,
        ["/smoothie"],
    )


COOKING = 3625241


@certifies("smoothie")
async def test_smoothie_cooked_by_recipe(world: World) -> None:
    world.game.on_text("/smoothie", ("smoothie", 3581573))
    world.game.on_text("🍹Готовить", ("smoothie_cooking", COOKING, 0))
    for version, drop in enumerate(("sm_drop_2", "sm_drop_4", "sm_drop_4", "sm_drop_1"), start=1):
        world.game.on_click(drop, edit=("smoothie_cooking", COOKING, version))
    world.game.on_click("sm_drop_5", edit=("smoothie_cooking", COOKING, 5))
    world.game.on_click("smoothie_accept", edit=("smoothie_cooking", COOKING, 6))
    result = await run_scenario(
        smoothie, context(world), CharacterState(), {"recipe": "🍇🥕🥕🍋🍅"}
    )
    # Рецепт того дня не совпал: смузи сварен без бонуса.
    assert (result.status, result.reason) == ("done", "no_bonus")
    assert world.game.payloads() == [
        "/smoothie",
        "🍹Готовить",
        "sm_drop_2",
        "sm_drop_4",
        "sm_drop_4",
        "sm_drop_1",
        "sm_drop_5",
        "smoothie_accept",
    ]
    assert world.gateway.lease is None


@certifies("smoothie")
async def test_smoothie_stops_when_drop_not_confirmed(world: World) -> None:
    world.game.on_text("/smoothie", ("smoothie", 3581573))
    world.game.on_text("🍹Готовить", ("smoothie_cooking", COOKING, 0))
    result = await run_scenario(
        smoothie, context(world), CharacterState(), {"recipe": "🍇🥕🥕🍋🍅"}
    )
    assert (result.status, result.reason) == ("stopped", "timeout")
    assert world.game.payloads() == ["/smoothie", "🍹Готовить", "sm_drop_2"]


def _report_of(day: date, msg_id: int) -> IncomingMessage:
    """Отчёт корпуса с датой битвы `day`."""
    msg = game_msg("crew", msg_id)
    stamp = f"{day.day}.{day.month:02d}.{day.year % 100}"
    text = re.sub(r"фабрику \d+\.\d+\.\d+:", f"фабрику {stamp}:", msg.text or "")
    return replace(msg, text=text)


@certifies("factory_report")
@pytest.mark.parametrize(("report", "reason"), [(3620025, "won"), (3625108, "lost")])
async def test_factory_report_of_today(world: World, report: int, reason: str) -> None:
    # /fb отвечает отчётом (все отчёты корпуса пришли сразу после /fb). «Сегодня» — по часам мира
    # (полдень MSK): полночь посреди теста день не сменит.
    clock = ShiftClock()
    world.game.clock = clock
    today = tasks_day(clock.now())
    world.game.on_text("/fb", _report_of(today, report))
    result = await run_scenario(factory_report, context(world), CharacterState(), {})
    assert (result.status, result.reason) == ("done", reason)
    assert world.game.payloads() == ["/fb"]
    seen = world.state.factory_report_day
    assert seen is not None and seen.value == today


@certifies("factory_report")
async def test_factory_report_of_other_day_is_nothing(world: World) -> None:
    # 26.09 02:23 /fb отдал отчёт о битве 25.09: последняя битва с участием персонажа.
    world.game.on_text("/fb", ("crew", 3625108))
    result = await run_scenario(factory_report, context(world), CharacterState(), {})
    assert (result.status, result.reason) == ("nothing", "old_report")


@certifies("factory_report")
async def test_factory_report_without_answer_fails(world: World) -> None:
    result = await run_scenario(factory_report, context(world), CharacterState(), {})
    assert (result.status, result.reason, world.game.payloads()) == ("failed", "timeout", ["/fb"])
