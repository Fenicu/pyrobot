import pytest

from app.engine.scenarios.library import deed, free_item, refresh, run_scenario
from app.engine.state.model import CharacterState
from tests.engine.fakegame import World
from tests.engine.scenarios.certify import certifies
from tests.engine.scenarios.conftest import context

STARTS = {
    "harvest": ("/harvest", 3517276),
    "job": ("/job", 3623881),
    "learn": ("/learns", 3603614),
    "dconv": ("/dconv", 3624728),
    "eat": ("/eat", 3516647),
}


@certifies("deed:harvest", "deed:job", "deed:learn", "deed:dconv", "deed:eat")
@pytest.mark.parametrize("activity", sorted(STARTS))
async def test_deed_started(world: World, activity: str) -> None:
    command, fixture = STARTS[activity]
    world.game.on_text(command, ("activities", fixture))
    result = await run_scenario(deed, context(world), CharacterState(), {"activity": activity})
    assert (result.status, world.game.payloads()) == ("done", [command])
    busy = world.state.busy
    assert busy is not None and busy.value is not None and busy.value.activity == activity


@certifies("deed:harvest")
@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        (3517360, "busy"),
        (3518565, "no_motivation"),
        (3517896, "no_money"),
        (3520502, "battle_soon"),
    ],
)
async def test_deed_refused(world: World, fixture: int, reason: str) -> None:
    world.game.on_text("/harvest", ("refusals", fixture))
    result = await run_scenario(deed, context(world), CharacterState(), {"activity": "harvest"})
    assert (result.status, result.reason) == ("refused", reason)


@certifies("deed:harvest")
async def test_deed_no_answer_is_failed(world: World) -> None:
    result = await run_scenario(deed, context(world), CharacterState(), {"activity": "harvest"})
    assert (result.status, result.reason) == ("failed", "timeout")


async def test_uncertified_is_suppressed(world: World) -> None:
    world.game.on_text("/harvest", ("activities", 3517276))
    ctx = context(world, simulate=True)
    result = await run_scenario(deed, ctx, CharacterState(), {"activity": "harvest"})
    assert (result.status, result.reason, world.game.payloads()) == (
        "suppressed",
        "uncertified",
        [],
    )


ITEMS = {
    "book": ("/read_exp", ("items", 3516680)),
    "card": ("/use_card", ("items", 3516678)),
    "prizebox": ("/unbox", ("items", 3517262)),
    "container_small": ("/unbox_ls", ("items", 3517971)),
}


@certifies("book", "card", "prizebox", "container_small")
@pytest.mark.parametrize("item", sorted(ITEMS))
async def test_free_item_done(world: World, item: str) -> None:
    command, ref = ITEMS[item]
    world.game.on_text(command, ref)
    result = await run_scenario(free_item, context(world), CharacterState(), {"item": item})
    assert (result.status, world.game.payloads()) == ("done", [command])


@certifies("card", "prizebox", "container_small", "book")
@pytest.mark.parametrize(
    ("item", "fixture", "reason"),
    [
        ("card", 3577823, "card_cooldown"),
        ("prizebox", 3520789, "prizebox_locked"),
        ("container_small", 3535591, "no_such_gift"),
        ("book", 3517617, "busy"),
    ],
)
async def test_free_item_refused(world: World, item: str, fixture: int, reason: str) -> None:
    world.game.on_text(ITEMS[item][0], ("refusals", fixture))
    result = await run_scenario(free_item, context(world), CharacterState(), {"item": item})
    assert (result.status, result.reason) == ("refused", reason)


REFRESHES = {
    "profile": ("😎Я", ("profile", 3624478)),
    "inventory": ("/inv", ("items", 3625102)),
    "food": ("/to_eat", ("food", 3624997)),
    "gifts": ("/gifts", ("items", 3623585)),
    "gorbushka": ("/gorbushka", ("gorbushka", 3516741)),
}


@certifies("refresh")
@pytest.mark.parametrize("source", sorted(REFRESHES))
async def test_refresh(world: World, source: str) -> None:
    command, ref = REFRESHES[source]
    world.game.on_text(command, ref)
    result = await run_scenario(refresh, context(world), CharacterState(), {"source": source})
    assert (result.status, world.game.payloads()) == ("done", [command])


async def test_refresh_is_sent_in_dry_run_and_simulation(world: World) -> None:
    world.game.on_text("😎Я", ("profile", 3624478))
    ctx = context(world, simulate=True)
    result = await run_scenario(refresh, ctx, CharacterState(), {"source": "profile"})
    assert result.status == "done"
    assert world.state.money is not None
