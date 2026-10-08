import pytest

from app.engine.scenarios.library import TANGERINE_GIFTS_BATCH, run_scenario, tangerine_gifts
from app.engine.state.model import CharacterState
from tests.engine import gift_texts as g
from tests.engine.fakegame import LIVE, World, running_world
from tests.engine.scenarios.certify import certifies
from tests.engine.scenarios.conftest import context

SHOP = "🎁 за 10🍊"


def gifts_screen(gifts: int, tangerines: int) -> str:
    text = g.GIFTS_WITH_TANGERINE_GIFTS
    text = text.replace("🎁Твои за 🍊: 1 шт.", f"🎁Твои за 🍊: {gifts} шт.")
    return text.replace("🍊У тебя: 2 шт.", f"🍊У тебя: {tangerines} шт.")


# Экран выбора на 42🍊: варианты 1–4 (так игра показывает максимум 4).
SHOP_42 = g.SHOP.replace("У тебя: 237🍊", "У тебя: 42🍊").replace("от 1 до 23", "от 1 до 4")


def _shop(world: World, gifts: int = 0, tangerines: int = 42) -> None:
    world.game.on_text("/gifts", g.gift_msg(gifts_screen(gifts, tangerines)))
    world.game.on_text(SHOP, g.gift_msg(SHOP_42, buttons=g.options(1, 2, 3, 4)))
    world.game.on_click("g_tangerines_small_4", edit=g.gift_msg(g.BOUGHT_ALL))


@certifies("tangerine_gifts")
async def test_buys_max_and_opens_every_gift(world: World) -> None:
    _shop(world)
    world.game.on_text("/unbox_t", g.gift_msg(g.OPENED))
    result = await run_scenario(tangerine_gifts, context(world), CharacterState(), {})
    assert (result.status, world.game.payloads()) == (
        "done",
        ["/gifts", SHOP, "g_tangerines_small_4", *["/unbox_t"] * 4],
    )
    state = world.state
    assert state.tangerine_gifts is not None and state.tangerine_gifts.value == 0
    assert state.tangerines is not None and state.tangerines.value == 2
    assert world.gateway.lease is None


@certifies("tangerine_gifts")
async def test_busy_only_buys(world: World) -> None:
    _shop(world)
    result = await run_scenario(tangerine_gifts, context(world), CharacterState(), {"open": False})
    assert (result.status, world.game.payloads()) == (
        "done",
        ["/gifts", SHOP, "g_tangerines_small_4"],
    )


@certifies("tangerine_gifts")
async def test_opens_without_buying_when_short(world: World) -> None:
    world.game.on_text("/gifts", g.gift_msg(gifts_screen(2, 9)))
    world.game.on_text("/unbox_t", g.gift_msg(g.OPENED))
    result = await run_scenario(tangerine_gifts, context(world), CharacterState(), {})
    assert (result.status, world.game.payloads()) == ("done", ["/gifts", "/unbox_t", "/unbox_t"])


@certifies("tangerine_gifts")
async def test_opens_one_batch_per_run(world: World) -> None:
    world.game.on_text("/gifts", g.gift_msg(gifts_screen(TANGERINE_GIFTS_BATCH + 5, 0)))
    world.game.on_text("/unbox_t", g.gift_msg(g.OPENED))
    result = await run_scenario(tangerine_gifts, context(world), CharacterState(), {})
    assert result.status == "done"
    assert world.game.payloads() == ["/gifts", *["/unbox_t"] * TANGERINE_GIFTS_BATCH]


@certifies("tangerine_gifts")
@pytest.mark.parametrize(
    ("gifts", "tangerines", "params", "reason"),
    [(0, 9, {}, "no_gifts"), (3, 9, {"open": False}, "busy")],
    ids=["nothing_to_do", "busy_nothing_to_buy"],
)
async def test_nothing(
    world: World, gifts: int, tangerines: int, params: dict[str, bool], reason: str
) -> None:
    world.game.on_text("/gifts", g.gift_msg(gifts_screen(gifts, tangerines)))
    result = await run_scenario(tangerine_gifts, context(world), CharacterState(), params)
    assert (result.status, result.reason, world.game.payloads()) == (
        "nothing",
        reason,
        ["/gifts"],
    )


@certifies("tangerine_gifts")
async def test_shop_without_options_opens_what_is_there(world: World) -> None:
    # 🍊 на экране подарков хватало, а экран покупки уже без кнопок: покупки нет.
    world.game.on_text("/gifts", g.gift_msg(gifts_screen(1, 10)))
    world.game.on_text(SHOP, g.gift_msg(g.SHORT.replace("🎁У тебя: 0 шт.", "🎁У тебя: 1 шт.")))
    world.game.on_text("/unbox_t", g.gift_msg(g.OPENED))
    result = await run_scenario(tangerine_gifts, context(world), CharacterState(), {})
    assert (result.status, world.game.payloads()) == ("done", ["/gifts", SHOP, "/unbox_t"])


@certifies("tangerine_gifts")
@pytest.mark.parametrize(
    ("text", "reason"), [(g.BUSY, "busy"), (g.NO_GIFT, "no_such_gift")], ids=["busy", "no_gift"]
)
async def test_first_open_refused(world: World, text: str, reason: str) -> None:
    world.game.on_text("/gifts", g.gift_msg(gifts_screen(3, 0)))
    world.game.on_text("/unbox_t", g.gift_msg(text))
    result = await run_scenario(tangerine_gifts, context(world), CharacterState(), {})
    assert (result.status, result.reason, world.game.payloads()) == (
        "refused",
        reason,
        ["/gifts", "/unbox_t"],
    )


@certifies("tangerine_gifts")
async def test_refusal_after_purchase_keeps_done(world: World) -> None:
    # Купил, а открыть не дал «занят»: покупка — уже итог запуска, остальное — следующим.
    _shop(world)
    world.game.on_text("/unbox_t", g.gift_msg(g.BUSY))
    result = await run_scenario(tangerine_gifts, context(world), CharacterState(), {})
    assert (result.status, result.reason) == ("done", "busy")
    assert world.game.payloads() == ["/gifts", SHOP, "g_tangerines_small_4", "/unbox_t"]


async def test_feature_off_blocks_purchase() -> None:
    settings = LIVE.model_copy(
        update={"features": LIVE.features.model_copy(update={"tangerine_gifts": False})}
    )
    async for w in running_world(settings):
        _shop(w)
        result = await run_scenario(tangerine_gifts, context(w), CharacterState(), {})
        assert (result.status, result.reason) == ("stopped", "feature_off:tangerine_gifts")
        assert w.game.payloads() == ["/gifts"]


@certifies("tangerine_gifts")
async def test_safe_point_only_between_openings(world: World) -> None:
    # Покупка и первое открытие идут подряд, пауза ловит только перед вторым /unbox_t.
    _shop(world)
    world.game.on_text("/unbox_t", g.gift_msg(g.OPENED))
    result = await run_scenario(tangerine_gifts, context(world, paused=True), CharacterState(), {})
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == ["/gifts", SHOP, "g_tangerines_small_4", "/unbox_t"]
