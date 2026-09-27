import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.engine.scenarios.context import ScenarioContext
from app.engine.scenarios.library import run_scenario
from app.engine.scenarios.lottery import lottery_buy, quantity
from app.engine.state.model import CharacterState
from app.engine.types import Button, IncomingMessage
from tests.engine.fakegame import GAME, Ref, World
from tests.engine.scenarios.certify import certifies
from tests.engine.scenarios.conftest import context
from tests.fixtures import game_msg

SCREEN = ("lottery", 3625282)
BOUGHT = ("lottery", 3625321)
NOTHING = {f"tickets_{c}": 0 for c in ("money", "knowledge", "raw", "details")}


def edited(ref: tuple[str, int], *pairs: tuple[str, str]) -> IncomingMessage:
    msg = game_msg(*ref)
    text = msg.text or ""
    for old, new in pairs:
        assert old in text
        text = text.replace(old, new)
    return replace(msg, text=text)


async def buy(world: World, params: dict[str, Any] | None = None, **kw: Any) -> tuple[str, str]:
    ctx = kw.pop("ctx", None) or context(world, **kw)
    result = await run_scenario(lottery_buy, ctx, CharacterState(), params or {})
    assert world.gateway.lease is None
    return result.status, result.reason


@certifies("lottery_buy")
async def test_buys_all_with_one_command(world: World) -> None:
    # Живой тираж 3285 26.09: экран и ответ «Купить все» — всё до лимита 10/7/7/7.
    world.game.on_text("/tickets", SCREEN)
    world.game.on_text("/tickets_all", BOUGHT)
    assert await buy(world) == ("done", "bought_all")
    assert world.game.payloads() == ["/tickets", "/tickets_all"]
    lottery = world.state.lottery
    assert lottery is not None and lottery.value.bought == {
        "money": 10,
        "knowledge": 7,
        "raw": 7,
        "details": 7,
    }
    money = world.state.money
    assert money is not None and money.value == 675 - 300


@certifies("lottery_buy")
@pytest.mark.parametrize(
    ("money", "params"),
    [
        # Денег на 3 билета из 10: цели «до лимита» не хватает — по валютам, не «Купить все».
        ("$100", {}),
        # За деньги не брать, а на билет денег и нет: «Купить все» купило бы 💵, если деньги
        # придут до отправки.
        ("$10", {"tickets_money": 0}),
        # Запас не даёт купить ни одного 💵-билета.
        ("$20", {"keep_money": 10}),
    ],
)
async def test_buy_all_only_when_every_goal_is_to_limit_and_affordable(
    world: World, money: str, params: dict[str, Any]
) -> None:
    world.game.on_text("/tickets", edited(SCREEN, ("💵Деньги: $675", f"💵Деньги: {money}")))
    world.game.on_text("/tickets_all", BOUGHT)
    await buy(world, params)
    assert "/tickets_all" not in world.game.payloads()
    assert world.game.payloads()[0] == "/tickets" and len(world.game.payloads()) > 1


@certifies("lottery_buy")
@pytest.mark.parametrize(
    ("params", "first"),
    [
        # Резерв билета Горбушки и отеля: «Купить все» потратило бы его.
        ({"reserve": 600}, "💵 => 🤑"),
        ({"keep_details": 136_669}, "💵 => 🤑"),
        # За деньги не брать совсем: «Купить все» купило бы.
        ({"tickets_money": 0}, "📚 => 🤑"),
        ({"tickets_knowledge": 3}, "💵 => 🤑"),
    ],
)
async def test_other_goals_buy_per_currency(
    world: World, params: dict[str, Any], first: str
) -> None:
    world.game.on_text("/tickets", SCREEN)
    world.game.on_text("/tickets_all", BOUGHT)
    await buy(world, params)
    assert world.game.payloads()[:2] == ["/tickets", first]
    assert "/tickets_all" not in world.game.payloads()


LIVE_SCREEN = ("lottery", 3626217)
MONEY_SCREEN = ("lottery", 3626219, 0)
MONEY_CLICKED = ("lottery", 3626219, 1)
ONLY_MONEY = {**NOTHING, "tickets_money": 1}


def money_frame(
    bought: int, buttons: tuple[int, ...], template: Ref = MONEY_CLICKED
) -> IncomingMessage:
    """Кадр экрана 💵 из живого съёма с другим «Куплено» и кнопками (1/3/5/7 и остаток)."""
    frame = game_msg(*template)
    assert frame.text is not None
    quantities = [Button(str(n), 0, i, f"tickets_money_{n}") for i, n in enumerate(buttons)]
    inline = (*quantities, Button("🚫Отменить", 1, 0, "cancel_inline"))
    old = next(line for line in frame.text.split("\n") if line.startswith("Куплено: "))
    text = frame.text.replace(old, f"Куплено: {bought} из 10")
    return replace(frame, text=text, inline=inline)


@certifies("lottery_buy")
async def test_buys_one_currency_with_quantity_button(world: World) -> None:
    # Живой съём 27.09: /tickets → 💵 => 🤑 (новое сообщение с кнопками) → «1» → правка «1 из 10».
    world.game.on_text("/tickets", LIVE_SCREEN)
    world.game.on_text("💵 => 🤑", MONEY_SCREEN)
    world.game.on_click("tickets_money_1", edit=MONEY_CLICKED)
    assert await buy(world, ONLY_MONEY) == ("done", "bought_each")
    assert world.game.payloads() == ["/tickets", "💵 => 🤑", "tickets_money_1"]
    # Клик — по сообщению экрана валюты, его и правит игра.
    clicked = world.game.messages[world.game.sent[-1].message_id or 0]
    assert clicked.kind == "edit" and "Куплено: 1 из 10" in (clicked.text or "")
    state = world.state
    assert state.lottery is not None and state.lottery.value.bought is not None
    assert state.lottery.value.bought["money"] == 1
    assert state.money is not None and state.money.value == 4785 - 30


@certifies("lottery_buy")
async def test_quantity_buttons_are_picked_greedily(world: World) -> None:
    # Цель 4: «3» (наибольшая не больше 4), по новой правке — «1».
    world.game.on_text("/tickets", LIVE_SCREEN)
    world.game.on_text("💵 => 🤑", MONEY_SCREEN)
    world.game.on_click("tickets_money_3", edit=money_frame(3, (1, 3, 5, 7)))
    world.game.on_click("tickets_money_1", edit=money_frame(4, (1, 3, 5, 6)))
    assert await buy(world, {**NOTHING, "tickets_money": 4}) == ("done", "bought_each")
    assert world.game.payloads() == [
        "/tickets",
        "💵 => 🤑",
        "tickets_money_3",
        "tickets_money_1",
    ]


@certifies("lottery_buy")
async def test_currency_screen_recounts_goal_by_its_bought(world: World) -> None:
    # Экран тиража показал 0 за 💵, а пока шёл экран валюты, с телефона купили 2: цель 3 —
    # докупается один билет, а не три.
    world.game.on_text("/tickets", LIVE_SCREEN)
    world.game.on_text("💵 => 🤑", money_frame(2, (1, 3, 5, 7, 8), MONEY_SCREEN))
    world.game.on_click("tickets_money_1", edit=money_frame(3, (1, 3, 5, 7)))
    world.game.on_click("tickets_money_3", edit=money_frame(5, (1, 3, 5)))
    assert await buy(world, {**NOTHING, "tickets_money": 3}) == ("done", "bought_each")
    assert world.game.payloads() == ["/tickets", "💵 => 🤑", "tickets_money_1"]


@certifies("lottery_buy")
async def test_currency_already_at_goal_buys_nothing(world: World) -> None:
    # Пока шёл экран валюты, с телефона докупили до цели: не нехватка, а цель достигнута.
    world.game.on_text("/tickets", LIVE_SCREEN)
    world.game.on_text("💵 => 🤑", money_frame(3, (1, 3, 5, 7), MONEY_SCREEN))
    assert await buy(world, {**NOTHING, "tickets_money": 3}) == ("nothing", "target_reached")
    assert world.game.payloads() == ["/tickets", "💵 => 🤑"]


@certifies("lottery_buy")
async def test_no_fitting_quantity_button_fails(world: World) -> None:
    # Экран валюты без кнопок количества: покупать нечем — неудача (пауза повтора растёт,
    # уведомление одно), а не «нечего делать» каждую минуту.
    world.game.on_text("/tickets", LIVE_SCREEN)
    world.game.on_text("💵 => 🤑", replace(game_msg(*MONEY_SCREEN), inline=()))
    assert await buy(world, ONLY_MONEY) == ("failed", "no_quantity_button")
    assert world.game.payloads() == ["/tickets", "💵 => 🤑"]


def test_quantity_choice_on_live_frames() -> None:
    opened, clicked = (game_msg(*MONEY_SCREEN), game_msg(*MONEY_CLICKED))
    assert quantity(opened, 10) == (10, "tickets_money_10")
    assert quantity(opened, 4) == (3, "tickets_money_3")
    assert quantity(clicked, 9) == (9, "tickets_money_9")
    assert quantity(clicked, 2) == (1, "tickets_money_1")
    assert quantity(clicked, 0) is None


@certifies("lottery_buy")
async def test_next_currency_after_safe_point_rereads_screen(world: World) -> None:
    # 💵 купили, между валютами — безопасная точка и снова экран; 📚 уже на лимите (живой ответ).
    world.game.on_text("/tickets", LIVE_SCREEN)
    world.game.on_text("💵 => 🤑", MONEY_SCREEN)
    world.game.on_click("tickets_money_1", edit=MONEY_CLICKED)
    world.game.on_text("📚 => 🤑", ("lottery", 3626225))
    params = {**ONLY_MONEY, "tickets_knowledge": "max"}
    assert await buy(world, params) == ("done", "bought_each")
    assert world.game.payloads() == [
        "/tickets",
        "💵 => 🤑",
        "tickets_money_1",
        "/tickets",
        "📚 => 🤑",
    ]


@certifies("lottery_buy")
async def test_pause_stops_between_currencies(world: World) -> None:
    world.game.on_text("/tickets", LIVE_SCREEN)
    world.game.on_text("💵 => 🤑", MONEY_SCREEN)
    world.game.on_click("tickets_money_1", edit=MONEY_CLICKED)
    ctx = ScenarioContext(
        world.gateway,
        game_chat_id=GAME,
        simulate=False,
        paused=lambda: len(world.game.sent) >= 3,
        timeout_s=0.3,
    )
    params = {**ONLY_MONEY, "tickets_knowledge": "max"}
    assert await buy(world, params, ctx=ctx) == ("stopped", "paused")
    assert world.game.payloads() == ["/tickets", "💵 => 🤑", "tickets_money_1"]


@certifies("lottery_buy")
@pytest.mark.parametrize(
    ("answer", "tickets", "result"),
    [
        # Игра считает, что 💵 не хватает (куплено 4, цель 10): кнопок нет, покупать нечего.
        (("lottery", 3402013), 10, ("nothing", "cant_afford")),
        # «Не хватает» при «Куплено: 10 из 10»: цель в 1 билет уже достигнута.
        (("lottery", 3385821), 1, ("nothing", "target_reached")),
        (("lottery", 1640401), 1, ("refused", "lottery_closed")),
        (("refusals", 3626159), 1, ("failed", "wrong_screen")),
    ],
)
async def test_currency_screen_without_purchase(
    world: World, answer: tuple[str, int], tickets: int, result: tuple[str, str]
) -> None:
    world.game.on_text("/tickets", LIVE_SCREEN)
    world.game.on_text("💵 => 🤑", answer)
    assert await buy(world, {**NOTHING, "tickets_money": tickets}) == result
    assert world.game.payloads() == ["/tickets", "💵 => 🤑"]


@certifies("lottery_buy")
async def test_screen_of_other_currency_is_not_accepted(world: World) -> None:
    world.game.on_text("/tickets", LIVE_SCREEN)
    world.game.on_text("💵 => 🤑", ("lottery", 3626225))
    assert await buy(world, ONLY_MONEY) == ("failed", "timeout")


@certifies("lottery_buy")
async def test_click_without_edit_fails(world: World) -> None:
    world.game.on_text("/tickets", LIVE_SCREEN)
    world.game.on_text("💵 => 🤑", MONEY_SCREEN)
    assert await buy(world, ONLY_MONEY) == ("failed", "timeout")
    assert world.game.payloads() == ["/tickets", "💵 => 🤑", "tickets_money_1"]


@certifies("lottery_buy")
async def test_target_reached_and_cant_afford_read_screen_only(world: World) -> None:
    world.game.on_text("/tickets", SCREEN)
    world.game.on_text("/tickets", edited(SCREEN, ("💵Деньги: $675", "💵Деньги: $10")))
    assert await buy(world, NOTHING) == ("nothing", "target_reached")
    money_only = {**NOTHING, "tickets_money": "max"}
    assert await buy(world, money_only) == ("nothing", "cant_afford")
    assert world.game.payloads() == ["/tickets", "/tickets"]


@certifies("lottery_buy")
@pytest.mark.parametrize(
    ("answer", "reason"),
    [(("lottery", 3624975), "no_draw"), (("lottery", 1640401), "lottery_closed")],
)
async def test_no_draw_or_closed_sale(world: World, answer: tuple[str, int], reason: str) -> None:
    world.game.on_text("/tickets", answer)
    assert await buy(world) == ("nothing", reason)
    assert world.game.payloads() == ["/tickets"]


@certifies("lottery_buy")
async def test_buy_all_that_bought_nothing_is_nothing(world: World) -> None:
    # Живой ответ «Куплено 0 билетов.» (тираж 3041) — в тираже экрана.
    world.game.on_text("/tickets", SCREEN)
    world.game.on_text("/tickets_all", edited(("lottery", 3525365), ("3041 тираж", "3285 тираж")))
    assert await buy(world) == ("nothing", "bought_none")
    assert world.game.payloads() == ["/tickets", "/tickets_all"]


@certifies("lottery_buy")
async def test_cant_afford_notes_every_short_currency(world: World) -> None:
    # Запасы не дают ни одного билета 💵 и ⚙️: обе — в нехватку с ресурсом с экрана, чтобы
    # планировщик ждал его роста, а не открывал экран по устареванию.
    world.game.on_text("/tickets", SCREEN)
    params = {"tickets_knowledge": 0, "tickets_raw": 0, "keep_money": 675, "keep_details": 136_669}
    result = await run_scenario(lottery_buy, context(world), CharacterState(), params)
    assert (result.status, result.reason) == ("nothing", "cant_afford")
    assert result.details == {
        "lottery": {"draw": 3285, "short": {"money": 675, "details": 136_669}}
    }
    assert world.game.payloads() == ["/tickets"]


@certifies("lottery_buy")
async def test_closed_answer_to_buy_is_final(world: World) -> None:
    world.game.on_text("/tickets", SCREEN)
    world.game.on_text("/tickets_all", ("lottery", 1640401))
    assert await buy(world) == ("refused", "lottery_closed")
    assert world.game.payloads() == ["/tickets", "/tickets_all"]


@certifies("lottery_buy")
async def test_answer_of_other_draw_is_refused(world: World) -> None:
    world.game.on_text("/tickets", SCREEN)
    world.game.on_text("/tickets_all", ("lottery", 3536223))
    assert await buy(world) == ("refused", "draw_changed")


@certifies("lottery_buy")
async def test_help_instead_of_screen_is_wrong_screen(world: World) -> None:
    world.game.on_text("/tickets", ("refusals", 3626159))
    assert await buy(world) == ("failed", "wrong_screen")


class Later:
    """Часы сценария на два часа впереди: продажа по экрану уже закрыта."""

    def now(self) -> datetime:
        return datetime.now(UTC) + timedelta(hours=2)

    def monotonic(self) -> float:
        return time.monotonic()


@certifies("lottery_buy")
async def test_sale_end_checked_before_buying(world: World) -> None:
    world.game.on_text("/tickets", SCREEN)
    world.game.on_text("/tickets_all", BOUGHT)
    ctx = ScenarioContext(
        world.gateway,
        game_chat_id=GAME,
        simulate=False,
        paused=lambda: False,
        timeout_s=0.3,
        clock=Later(),
    )
    assert await buy(world, ctx=ctx) == ("nothing", "sale_closed")
    assert await buy(world, ONLY_MONEY, ctx=ctx) == ("nothing", "sale_closed")
    assert world.game.payloads() == ["/tickets", "/tickets"]


@certifies("lottery_buy")
async def test_bad_param_fails_before_buying(world: World) -> None:
    world.game.on_text("/tickets", SCREEN)
    assert await buy(world, {"tickets_money": "lots"}) == ("failed", "bad_param:tickets_money")
    assert await buy(world, {"reserve": -1}) == ("failed", "bad_param:reserve")
    assert world.game.payloads() == ["/tickets", "/tickets"]


@certifies("lottery_buy")
async def test_simulation_reads_screen_but_does_not_buy(world: World) -> None:
    world.game.on_text("/tickets", SCREEN)
    world.game.on_text("/tickets_all", BOUGHT)
    assert await buy(world, simulate=True) == ("suppressed", "uncertified")
    assert await buy(world, ONLY_MONEY, simulate=True) == ("suppressed", "uncertified")
    assert world.game.payloads() == ["/tickets", "/tickets"]
