from dataclasses import replace

import pytest

from app.engine.scenarios.library import deed, free_item, refresh, run_scenario
from app.engine.state.model import CharacterState
from tests.engine.fakegame import World
from tests.engine.parsing.test_activities import DCONV_DOG, HARVEST_DOG, JOB_SHORT, LEARN_LIGHT
from tests.engine.scenarios.certify import certifies
from tests.engine.scenarios.conftest import context
from tests.fixtures import game_msg

STARTS = {
    "harvest": ("/harvest", 3517276),
    "job": ("/job", 3623881),
    "learn": ("/learns", 3603614),
    "dconv": ("/dconv", 3624728),
    "eat": ("/eat", 3516647),
    "walk": ("/walk", 3625686),
    "confa": ("/confa", 3437620),
}


@certifies(
    "deed:harvest",
    "deed:job",
    "deed:learn",
    "deed:dconv",
    "deed:eat",
    "deed:walk",
    "deed:confa",
)
@pytest.mark.parametrize("activity", sorted(STARTS))
async def test_deed_started(world: World, activity: str) -> None:
    command, fixture = STARTS[activity]
    world.game.on_text(command, ("activities", fixture))
    result = await run_scenario(deed, context(world), CharacterState(), {"activity": activity})
    assert (result.status, world.game.payloads()) == ("done", [command])
    busy = world.state.busy
    assert busy is not None and busy.value is not None and busy.value.activity == activity


@pytest.mark.parametrize(
    ("activity", "command", "text"),
    [
        ("harvest", "/harvest", HARVEST_DOG),
        ("dconv", "/dconv", DCONV_DOG),
        ("job", "/job", JOB_SHORT),
        ("learn", "/learns", LEARN_LIGHT),
    ],
    ids=["harvest_dog", "dconv_dog", "job_short", "learn_light"],
)
async def test_deed_started_live_variants(
    world: World, activity: str, command: str, text: str
) -> None:
    # На проде 05.10 старт с 🐕 не распознавался: сценарий ждал подтверждения до таймаута.
    world.game.on_text(command, replace(game_msg("activities", STARTS[activity][1]), text=text))
    result = await run_scenario(deed, context(world), CharacterState(), {"activity": activity})
    assert (result.status, world.game.payloads()) == ("done", [command])
    busy = world.state.busy
    assert busy is not None and busy.value is not None and busy.value.activity == activity


@certifies("deed:walk", "deed:confa")
@pytest.mark.parametrize(("activity", "finish"), [("walk", 3625689), ("confa", 3438035)])
async def test_walk_and_confa_finish_frees_and_updates_stats(
    world: World, activity: str, finish: int
) -> None:
    command, start = STARTS[activity]
    world.game.on_text(command, ("activities", start))
    result = await run_scenario(deed, context(world), CharacterState(), {"activity": activity})
    assert result.status == "done"
    await world.feed("activities", finish)
    state = world.state
    assert state.busy is not None and state.busy.value is None
    assert state.activity_stats[activity].count == 1


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
async def test_deed_harvest_without_profession_is_refusal(world: World) -> None:
    text = (
        "❌Ты не можешь отправиться за ресурсами. "
        "Добывать ресурсы могут только Барахольщик или Старьёвщик"
    )
    world.game.on_text("/harvest", replace(game_msg("refusals", 3518565), text=text, inline=()))
    result = await run_scenario(deed, context(world), CharacterState(), {"activity": "harvest"})
    assert (result.status, result.reason) == ("refused", "not_harvester")


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
}


@certifies("book", "card")
@pytest.mark.parametrize("item", sorted(ITEMS))
async def test_free_item_done(world: World, item: str) -> None:
    command, ref = ITEMS[item]
    world.game.on_text(command, ref)
    result = await run_scenario(free_item, context(world), CharacterState(), {"item": item})
    assert (result.status, world.game.payloads()) == ("done", [command])


@certifies("prizebox")
async def test_prizebox_opens_from_inventory_screen(world: World) -> None:
    # Инвентарь без таймера у коробки — игра принимает /unbox только с этого экрана.
    world.game.on_text("/inv", ("items", 3625715))
    world.game.on_text("/unbox", ("items", 3625717))
    result = await run_scenario(free_item, context(world), CharacterState(), {"item": "prizebox"})
    assert (result.status, world.game.payloads()) == ("done", ["/inv", "/unbox"])
    assert world.gateway.lease is None


@certifies("container_small")
async def test_container_small_opens_from_gifts_screen(world: World) -> None:
    world.game.on_text("/gifts", ("items", 3623585))
    world.game.on_text("/unbox_ls", ("items", 3517971))
    result = await run_scenario(
        free_item, context(world), CharacterState(), {"item": "container_small"}
    )
    assert (result.status, world.game.payloads()) == ("done", ["/gifts", "/unbox_ls"])
    assert world.gateway.lease is None


@certifies("container_medium")
async def test_container_medium_opens_from_gifts_screen(world: World) -> None:
    # Живая цепочка 16.08: 🎁Подарки со средним контейнером → /unbox_lm → «Ты открыл Средний».
    world.game.on_text("/gifts", ("items", 3611231))
    world.game.on_text("/unbox_lm", ("items", 3611233))
    result = await run_scenario(
        free_item, context(world), CharacterState(), {"item": "container_medium"}
    )
    assert (result.status, world.game.payloads()) == ("done", ["/gifts", "/unbox_lm"])
    assert world.gateway.lease is None


@certifies("container_medium")
async def test_container_medium_screen_early_exit(world: World) -> None:
    # Малые есть, средних нет: /unbox_lm не уходит.
    world.game.on_text("/gifts", ("items", 3623585))
    result = await run_scenario(
        free_item, context(world), CharacterState(), {"item": "container_medium"}
    )
    assert (result.status, result.reason, world.game.payloads()) == (
        "nothing",
        "no_containers",
        ["/gifts"],
    )
    assert world.gateway.lease is None


@certifies("prizebox")
@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        (3516676, "no_prizebox"),
        (3625102, "prizebox_locked"),
    ],
)
async def test_prizebox_screen_early_exit(world: World, fixture: int, reason: str) -> None:
    world.game.on_text("/inv", ("items", fixture))
    result = await run_scenario(free_item, context(world), CharacterState(), {"item": "prizebox"})
    assert (result.status, result.reason, world.game.payloads()) == ("nothing", reason, ["/inv"])
    assert world.gateway.lease is None


@certifies("container_small")
async def test_container_small_screen_early_exit(world: World) -> None:
    world.game.on_text("/gifts", ("items", 3516682))
    result = await run_scenario(
        free_item, context(world), CharacterState(), {"item": "container_small"}
    )
    assert (result.status, result.reason, world.game.payloads()) == (
        "nothing",
        "no_containers",
        ["/gifts"],
    )
    assert world.gateway.lease is None


OPEN_COMMANDS = {
    "book": "/read_exp",
    "card": "/use_card",
    "prizebox": "/unbox",
    "container_small": "/unbox_ls",
    "container_medium": "/unbox_lm",
}
# Предметы, открытие которых игра принимает только с определённого экрана: экран
# показывается заранее и не выглядит противоречащим последующему отказу игры.
OPEN_SCREENS: dict[str, tuple[str, int]] = {
    "prizebox": ("/inv", 3625715),
    "container_small": ("/gifts", 3623585),
    "container_medium": ("/gifts", 3611231),
}


@certifies("card", "prizebox", "container_small", "container_medium", "book")
@pytest.mark.parametrize(
    ("item", "fixture", "reason"),
    [
        ("card", 3577823, "card_cooldown"),
        ("prizebox", 3520789, "prizebox_locked"),
        ("container_small", 3535591, "no_such_gift"),
        ("container_medium", 3535591, "no_such_gift"),
        ("book", 3517617, "busy"),
    ],
)
async def test_free_item_refused(world: World, item: str, fixture: int, reason: str) -> None:
    if item in OPEN_SCREENS:
        screen_command, screen_fixture = OPEN_SCREENS[item]
        world.game.on_text(screen_command, ("items", screen_fixture))
    world.game.on_text(OPEN_COMMANDS[item], ("refusals", fixture))
    result = await run_scenario(free_item, context(world), CharacterState(), {"item": item})
    assert (result.status, result.reason) == ("refused", reason)


REFRESHES = {
    "profile": ("/compact", ("profile", 3624478)),
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
    world.game.on_text("/compact", ("profile", 3624478))
    ctx = context(world, simulate=True)
    result = await run_scenario(refresh, ctx, CharacterState(), {"source": "profile"})
    assert result.status == "done"
    assert world.state.money is not None


# Полный профиль (настройка персонажа в игре, у новых аккаунтов по умолчанию): «😎Я» отвечает им,
# а разбирается только компактный — его даёт `/compact` при любой настройке.
FULL_PROFILE = (
    "🎙CEO сообщает:\n"
    "Ты молодец. Ходи в битвы, не забывай про репорты. Приятной игры! /harvest - /del\n"
    "\n"
    "До следующей Битвы осталось 8ч. 44 мин.!\n"
    "\n"
    "[SU]\xa0Casadei (☣️Black Mesa) 🐕\n"
    "🎚Уровень: 71\n"
    "💡Опыт: 17\xa0432\xa0572 из 18\xa0155\xa0142\n"
    "💼Скупщик 16 (💠)\n"
    "💵Деньги: $220\n"
    "🌐Swcoin: 3\n"
    "📚Знания: 27\xa0241\n"
    "🔩Сырьё: 34\xa0247\n"
    "⚙️Детали: 37\xa0880\n"
    "📈Акции: 14\xa0272 (/stock)\n"
    "🔥Мотивация: 26 из 26 (/pr)\n"
    "🎒Рюкзак: 12 из 24 (/inventory)\n"
    "🍊67 (/gifts)\n"
    "\n"
    "Навыки\n"
    "🔨Практика: 39+230+173 (442)\n"
    "🎓Теория: 39+230+173 (442)\n"
    "🐿Хитрость: 37+159+119 (315)\n"
    "🐢Мудрость: 38+159+119 (316)\n"
    "🔋Выносливость: 100% /to_eat\n"
    "💪Крутизна: ⭐️⭐️⭐️ /cool\n"
    "\n"
    "Гаджеты\n"
    "⚫️25\xa0PA’ltishCo (+137🐢, +51🎓, 📿)\n"
    "⚫️25\xa0Хулитопы (+84🔨, +54🐿, +30🎓, 🧶)\n"
    "⚫️25\xa0SM-art (+100🎓, +51🔨, 💎)\n"
    "⚫️25\xa0RedRing (+88🔨, +88🐢, 💎)\n"
    "⚫️25\xa0M-Zhilетka (+137🐿, +51🔨, 📿)\n"
    "⚫️25\xa0iBlackM (+100🔨, +51🎓, 💎)\n"
    "⚫️25\xa0WeBook (+88🎓, +88🐿, 💎)\n"
    "⚫️25\xa0Хиджаб (+84🎓, +54🐢, +30🔨, 🧶)\n"
    "\n"
    "🛌Сон через какое-то время\n"
    "\n"
    "Занятие\n"
    " Ничем не занят\n"
    "\n"
    "Компактный профиль /compact\n"
    "Бонусы /bonuses\n"
    "Настройки /settings"
)


async def test_refresh_profile_of_character_with_full_profile(world: World) -> None:
    world.game.on_text("😎Я", replace(game_msg("profile", 3624478), text=FULL_PROFILE))
    world.game.on_text("/compact", ("profile", 3624478))
    result = await run_scenario(refresh, context(world), CharacterState(), {"source": "profile"})
    assert (result.status, world.game.payloads()) == ("done", ["/compact"])
    assert world.state.money is not None
