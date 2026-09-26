import pytest

from app.engine.scenarios.library import fastfood, gorbushka, levelup, run_scenario, sleep
from app.engine.state.model import CharacterState, Obs, Skills
from tests.engine.fakegame import World
from tests.engine.scenarios.certify import certifies
from tests.engine.scenarios.conftest import context


@certifies("fastfood")
async def test_fastfood_opens_menu_then_eats(world: World) -> None:
    world.game.on_text("/to_eat", ("food", 3521844))
    world.game.on_text("🌭Хот-дог", ("food", 3624983))
    result = await run_scenario(fastfood, context(world), CharacterState(), {"food": "hotdog"})
    assert (result.status, world.game.payloads()) == ("done", ["/to_eat", "🌭Хот-дог"])
    assert world.state.stamina is not None and world.state.stamina.value == 52
    assert world.gateway.lease is None


@certifies("fastfood")
@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        (3624999, "fastfood_cooldown"),
        (3590672, "eat_while_sleeping"),
        (3521846, "fastfood_while_eating"),
    ],
)
async def test_fastfood_refused(world: World, fixture: int, reason: str) -> None:
    world.game.on_text("/to_eat", ("food", 3624997))
    world.game.on_text("🍔Бургер", ("refusals", fixture))
    result = await run_scenario(fastfood, context(world), CharacterState(), {"food": "burger"})
    assert (result.status, result.reason) == ("refused", reason)


@certifies("fastfood")
async def test_fastfood_menu_missing_stops(world: World) -> None:
    result = await run_scenario(fastfood, context(world), CharacterState(), {"food": "hotdog"})
    assert (result.status, result.reason, world.game.payloads()) == (
        "stopped",
        "timeout",
        ["/to_eat"],
    )
    assert world.gateway.lease is None


@certifies("fastfood")
async def test_fastfood_paused_between_steps(world: World) -> None:
    world.game.on_text("/to_eat", ("food", 3521844))
    result = await run_scenario(
        fastfood, context(world, paused=True), CharacterState(), {"food": "hotdog"}
    )
    assert (result.status, result.reason, world.game.payloads()) == (
        "stopped",
        "paused",
        ["/to_eat"],
    )


def _skills(practice: int, theory: int, cunning: int, wisdom: int) -> CharacterState:
    from datetime import UTC, datetime

    return CharacterState(
        skills=Obs(
            value=Skills(practice=practice, theory=theory, cunning=cunning, wisdom=wisdom),
            at=datetime.now(UTC),
        )
    )


@certifies("levelup")
async def test_levelup_picks_lower_skills(world: World) -> None:
    world.game.on_text("/levelup", ("levelup", 3532816))
    world.game.on_text("+1 🔨Практика", ("levelup", 3532818))
    world.game.on_text("+1 🐿Хитрость", ("levelup", 3532820))
    result = await run_scenario(levelup, context(world), _skills(461, 462, 344, 345), {})
    assert result.status == "done"
    assert world.game.payloads() == ["/levelup", "+1 🔨Практика", "+1 🐿Хитрость"]


@certifies("levelup")
async def test_levelup_error_mid_way_is_refused(world: World) -> None:
    world.game.on_text("/levelup", ("levelup", 3532816))
    world.game.on_text("+1 🎓Теория", ("refusals", 3516893))
    result = await run_scenario(levelup, context(world), _skills(470, 460, 344, 345), {})
    assert (result.status, result.reason) == ("refused", "something_wrong")
    assert world.game.payloads() == ["/levelup", "+1 🎓Теория"]
    assert world.gateway.lease is None


@certifies("levelup")
async def test_levelup_no_menu_stops(world: World) -> None:
    result = await run_scenario(levelup, context(world), CharacterState(), {})
    assert (result.status, result.reason, world.game.payloads()) == (
        "stopped",
        "timeout",
        ["/levelup"],
    )


@certifies("gorbushka")
async def test_gorbushka_buy_ticket_and_fight(world: World) -> None:
    world.game.on_text("/gorbushka", ("gorbushka", 3593569))
    world.game.on_click("gorbushka_new", edit=("gorbushka", 3537930))
    world.game.on_click("gorbushka_new_accept", edit=("gorbushka", 3516738))
    world.game.on_click(
        "gorbushka_fight", edit=("gorbushka", 3516793), new=(("gorbushka", 3516739),)
    )
    result = await run_scenario(gorbushka, context(world), CharacterState(), {"buy": True})
    assert result.status == "done", result
    assert world.game.payloads() == [
        "/gorbushka",
        "gorbushka_new",
        "gorbushka_new_accept",
        "gorbushka_fight",
    ]
    assert world.gateway.lease is None


@certifies("gorbushka")
async def test_gorbushka_meeting_fight_only(world: World) -> None:
    world.game.on_text("/gorbushka", ("gorbushka", 3516738))
    world.game.on_click(
        "gorbushka_fight", edit=("gorbushka", 3516793), new=(("gorbushka", 3564182),)
    )
    result = await run_scenario(gorbushka, context(world), CharacterState(), {"buy": False})
    assert result.status == "done"
    assert world.game.payloads() == ["/gorbushka", "gorbushka_fight"]


@certifies("gorbushka")
@pytest.mark.parametrize(
    ("fixture", "buy", "reason"),
    [
        (3516741, False, "waiting"),
        (3516661, True, "done"),
        (3528135, False, "need_ticket"),
        (3528135, True, "cant_afford"),
        (3520526, True, "cant_afford"),
    ],
)
async def test_gorbushka_nothing_to_do(world: World, fixture: int, buy: bool, reason: str) -> None:
    world.game.on_text("/gorbushka", ("gorbushka", fixture))
    result = await run_scenario(gorbushka, context(world), CharacterState(), {"buy": buy})
    assert (result.status, result.reason) == ("nothing", reason)
    assert world.game.payloads() == ["/gorbushka"]


@certifies("gorbushka")
async def test_gorbushka_short_of_money_after_accept(world: World) -> None:
    world.game.on_text("/gorbushka", ("gorbushka", 3593569))
    world.game.on_click("gorbushka_new", edit=("gorbushka", 3537930))
    world.game.on_click("gorbushka_new_accept", edit=("gorbushka", 3520526))
    result = await run_scenario(gorbushka, context(world), CharacterState(), {"buy": True})
    assert (result.status, result.reason) == ("refused", "no_money")
    assert world.game.payloads() == ["/gorbushka", "gorbushka_new", "gorbushka_new_accept"]


@certifies("gorbushka")
async def test_gorbushka_fight_confirmed_only_by_result(world: World) -> None:
    world.game.on_text("/gorbushka", ("gorbushka", 3516738))
    world.game.on_click("gorbushka_fight", edit=("gorbushka", 3516793))
    result = await run_scenario(gorbushka, context(world), CharacterState(), {"buy": False})
    assert (result.status, result.reason) == ("failed", "timeout")


@certifies("gorbushka")
async def test_gorbushka_skills_changed_is_refusal(world: World) -> None:
    world.game.on_text("/gorbushka", ("gorbushka", 3516738))
    world.game.on_click("gorbushka_fight", edit=("gorbushka", 3524271))
    result = await run_scenario(gorbushka, context(world), CharacterState(), {"buy": False})
    assert (result.status, result.reason) == ("refused", "skills_changed")


@certifies("gorbushka")
async def test_gorbushka_busy_refusal(world: World) -> None:
    world.game.on_text("/gorbushka", ("refusals", 3517360))
    result = await run_scenario(gorbushka, context(world), CharacterState(), {"buy": True})
    assert (result.status, result.reason) == ("refused", "busy")


async def test_sleep_menu_then_hours_uncertified(world: World) -> None:
    world.game.on_text("🛌Спать", ("sleep", 3526861))
    result = await run_scenario(
        sleep, context(world, simulate=True), CharacterState(), {"hours": 7}
    )
    assert (result.status, result.reason) == ("suppressed", "uncertified")
    assert world.game.payloads() == ["🛌Спать"]
