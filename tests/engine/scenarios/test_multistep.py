import pytest

from app.engine.notify import Level
from app.engine.scenarios.library import fastfood, gorbushka, levelup, run_scenario, sleep
from app.engine.state.model import CharacterState, Obs, Skills
from tests.engine.fakegame import Ref, World
from tests.engine.scenarios.certify import certifies
from tests.engine.scenarios.conftest import context


class Notes:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def notify(self, level: Level, code: str, text: str) -> None:
        self.sent.append((level, code))


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
    world.game.on_text("/to_eat", ("food", 3521844))
    world.game.on_text("🍔Бургер", ("refusals", fixture))
    result = await run_scenario(fastfood, context(world), CharacterState(), {"food": "burger"})
    assert (result.status, result.reason) == ("refused", reason)


@certifies("fastfood")
async def test_fastfood_cooldown_in_menu_is_nothing(world: World) -> None:
    world.game.on_text("/to_eat", ("food", 3624997))
    world.game.on_text("🍔Бургер", ("refusals", 3624999))
    result = await run_scenario(fastfood, context(world), CharacterState(), {"food": "burger"})
    assert (result.status, result.reason) == ("nothing", "fastfood_cooldown")
    assert world.game.payloads() == ["/to_eat"]
    assert world.gateway.lease is None


@certifies("fastfood")
async def test_fastfood_menu_missing_stops(world: World) -> None:
    result = await run_scenario(fastfood, context(world), CharacterState(), {"food": "hotdog"})
    assert (result.status, result.reason, world.game.payloads()) == (
        "stopped",
        "timeout",
        ["/to_eat"],
    )
    assert world.gateway.lease is None


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


SLEEP = 3625590


def _sleep_menu(world: World) -> None:
    world.game.on_text("🛌Спать", ("sleep", SLEEP, 0))
    world.game.on_click("sleep_7", edit=("sleep", SLEEP, 1))


@certifies("sleep")
async def test_sleep_in_hotel(world: World) -> None:
    """Живой сон 26.09: меню → 7 часов → выбор места → отель; ответ «Ты потратился на отель…»
    приходит отдельным сообщением."""
    await world.feed("profile", 3624478)
    _sleep_menu(world)
    world.game.on_click("sleep_Hotel", edit=("sleep", SLEEP, 2), new=(("sleep", 3625591),))
    result = await run_scenario(
        sleep, context(world), CharacterState(), {"hours": 7, "hotel": True}
    )
    assert (result.status, result.reason) == ("done", "fell_asleep")
    assert world.game.payloads() == ["🛌Спать", "sleep_7", "sleep_Hotel"]
    assert world.state.money is not None and world.state.money.value == 867 - 213
    assert world.gateway.lease is None


@certifies("sleep")
async def test_sleep_under_bridge(world: World) -> None:
    # Экран выбора тот же; итог «под мостом» — из корпуса.
    _sleep_menu(world)
    world.game.on_click("sleep_Bridge", edit=("sleep", 3541942), new=(("sleep", 3541943),))
    result = await run_scenario(
        sleep, context(world), CharacterState(), {"hours": 7, "hotel": False}
    )
    assert (result.status, result.reason) == ("done", "fell_asleep")
    assert world.game.payloads() == ["🛌Спать", "sleep_7", "sleep_Bridge"]
    busy = world.state.busy
    assert busy is not None and busy.value is not None
    assert busy.value.activity == "sleep_bridge"


@certifies("sleep")
@pytest.mark.parametrize(("reserve", "place"), [(0, "sleep_Hotel"), (700, "sleep_Bridge")])
async def test_sleep_place_by_screen_price(world: World, reserve: int, place: str) -> None:
    """Цена отеля заранее неизвестна: берётся с экрана выбора (213 💵). 867 💵 хватает на отель;
    за вычетом резерва билета Горбушки — уже нет, мост."""
    await world.feed("profile", 3624478)
    _sleep_menu(world)
    world.game.on_click("sleep_Hotel", edit=("sleep", SLEEP, 2), new=(("sleep", 3625591),))
    world.game.on_click("sleep_Bridge", edit=("sleep", 3541942), new=(("sleep", 3541943),))
    params = {"hours": 7, "hotel_threshold": None, "ticket_reserve": reserve}
    result = await run_scenario(sleep, context(world), world.state, params)
    assert (result.status, result.reason) == ("done", "fell_asleep")
    assert world.game.payloads() == ["🛌Спать", "sleep_7", place]


@certifies("sleep")
@pytest.mark.parametrize("answer", [(("sleep", 3517441),), ()])
async def test_sleep_other_place_is_mismatch(world: World, answer: tuple[Ref, ...]) -> None:
    """Выбран отель, а персонаж уснул под мостом: принудительно на 12 часов (сон не выбран
    вовремя) или мост на 7 — не успех отеля."""
    notes = Notes()
    _sleep_menu(world)
    edit = None if answer else ("sleep", 3541942)
    world.game.on_click("sleep_Hotel", edit=edit, new=answer)
    result = await run_scenario(
        sleep, context(world, notifier=notes), CharacterState(), {"hours": 7, "hotel": True}
    )
    assert (result.status, result.reason) == ("refused", "place_mismatch")
    assert notes.sent == [("warn", "sleep_place_mismatch")]


@certifies("sleep")
async def test_sleep_hotel_without_money_refused(world: World) -> None:
    _sleep_menu(world)
    world.game.on_click("sleep_Hotel", edit=("sleep", 3541940))
    result = await run_scenario(
        sleep, context(world), CharacterState(), {"hours": 7, "hotel": True}
    )
    assert (result.status, result.reason) == ("refused", "no_money")


@certifies("sleep")
async def test_sleep_place_step_for_other_hours_stops(world: World) -> None:
    _sleep_menu(world)
    world.game.on_click("sleep_8", edit=("sleep", SLEEP, 1))
    result = await run_scenario(sleep, context(world), CharacterState(), {"hours": 8})
    assert (result.status, result.reason) == ("stopped", "timeout")
    assert world.game.payloads() == ["🛌Спать", "sleep_8"]


async def test_sleep_step_suppressed_when_simulated(world: World) -> None:
    world.game.on_text("🛌Спать", ("sleep", 3526861))
    result = await run_scenario(
        sleep, context(world, simulate=True), CharacterState(), {"hours": 7}
    )
    assert (result.status, result.reason) == ("suppressed", "uncertified")
    assert world.game.payloads() == ["🛌Спать"]
