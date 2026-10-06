"""Сертификация покупки и надевания гаджетов на живых текстах 06.10 (`gadget_texts`)."""

import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.engine.clock import Clock
from app.engine.scenarios.context import ScenarioContext
from app.engine.scenarios.gadgets import gadget_buy, gadget_wear_set
from app.engine.scenarios.library import ScenarioFn, run_scenario
from app.engine.state.model import CharacterState, GorbushkaState, Obs
from app.engine.types import IncomingMessage
from tests.engine.fakegame import GAME, LIVE, Ref, World, running_world
from tests.engine.gadget_texts import (
    BOUGHT_RIGHT1,
    INV_BOUGHT,
    NETWORK,
    NO_MONEY_RIGHT14,
    SHOP_MENU,
    SHOP_RIGHT,
    UNKNOWN,
    WEAR_P1,
    game_text,
)
from tests.engine.scenarios.certify import certifies
from tests.engine.scenarios.test_obligations import own_company
from tests.fixtures import game_msg

BUYING = LIVE.model_copy(
    update={"features": LIVE.features.model_copy(update={"gadgets_buy": True})}
)
NAV = ["🕸Сеть", "🏪Магазин", "📱Правая рука"]
P1 = "📱Китайская мобила (+1🔨)"
P1_UPGRADED = f"⚪️3\xa0{P1}"
NEW_P1 = f"{P1} /wear_11_p1"
BLACKM = "⚫️25\xa0📱iBlackM (+100🔨, +51🎓, 💎)"
StateFn = Callable[[], CharacterState]


@pytest.fixture
async def live_buying() -> AsyncIterator[World]:
    async for w in running_world(BUYING):
        await own_company(w)
        yield w


class At:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self) -> datetime:
        return self.moment

    def monotonic(self) -> float:
        return time.monotonic()


def context(
    world: World, scenario: str, state: StateFn | None = None, clock: Clock | None = None
) -> ScenarioContext:
    return ScenarioContext(
        world.gateway,
        game_chat_id=GAME,
        simulate=False,
        paused=lambda: False,
        timeout_s=0.3,
        clock=clock,
        scenario=scenario,
        state=state or (lambda: world.state),
        settings=lambda: world.settings.current,
    )


async def _run(
    fn: ScenarioFn,
    world: World,
    scenario: str,
    state: StateFn | None,
    clock: Clock | None,
    params: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    ctx = context(world, scenario, state, clock)
    result = await run_scenario(fn, ctx, world.state, params)
    assert world.gateway.lease is None
    return result.status, result.reason, result.details or {}


async def run(
    world: World, *, state: StateFn | None = None, clock: Clock | None = None, **params: Any
) -> tuple[str, str, dict[str, Any]]:
    return await _run(gadget_buy, world, "gadget_buy", state, clock, params)


async def run_set(
    world: World, *, state: StateFn | None = None, **params: Any
) -> tuple[str, str, dict[str, Any]]:
    return await _run(gadget_wear_set, world, "gadget_wear_set", state, None, params)


def shop(world: World, text: str = SHOP_RIGHT) -> None:
    world.game.on_text("🕸Сеть", game_text(NETWORK))
    world.game.on_text("🏪Магазин", game_text(SHOP_MENU))
    world.game.on_text("📱Правая рука", game_text(text))


def with_money(money: str) -> str:
    return SHOP_RIGHT.replace("💵Твои деньги: $555", f"💵Твои деньги: ${money}")


def bag_of(*lines: str) -> str:
    """`/inv` 12 с другим хвостом рюкзака вместо купленной мобилы."""
    return INV_BOUGHT.replace(NEW_P1, "\n".join(lines))


def worn_answer(line: str, tail: str = "🗳Сет Логистик") -> str:
    """Ответ на `/wear_` 13 о другом гаджете (в строке «Ты надел» — без значка слота) и с другими
    строками сетов в хвосте."""
    name = re.sub(r"\A\W+", "", line)
    text = WEAR_P1.replace("👍Ты надел Китайская мобила (+1🔨)", f"👍Ты надел {name}")
    return text.replace("\n\n🗳Сет Логистик\nБонусы", f"\n\n{tail}\nБонусы")


def sold(label: str, price: int, money: int, left: int, n: int) -> IncomingMessage:
    """Продажа из живого ответа 3625251 (📯Pied Piper, 1 шт.) с другими компанией и числами."""
    msg = game_msg("stocks", 3625251)
    text = (msg.text or "").replace("📯Pied Piper", label)
    text = text.replace("Цена 10 ", f"Цена {price} ").replace("$699", f"${money}")
    shares = f"📈Акции: {left} на ${left * price}"
    text = re.sub(r"📈Акции: [\d \xa0]*\d на \$[\d \xa0]*\d", shares, text)
    # Продано всё — строки «Ты можешь продать от … до …» в ответе нет.
    sellable = f"Ты можешь продать от 1 до {left} акций\n\n" if left else ""
    text = re.sub(r"Ты можешь продать от 1 до [\d \xa0]*\d акций\n\n", sellable, text)
    text = text.replace("Продано 1 ", f"Продано {n} ")
    return replace(msg, text=text)


def meeting(state: CharacterState) -> CharacterState:
    """Встреча с продаваном Горбушки: смена снаряжения под запретом."""
    seen = Obs(value=GorbushkaState(state="meeting"), at=datetime.now(UTC))
    return state.model_copy(update={"gorbushka": seen})


def guarded_after(world: World, payload: str) -> StateFn:
    """Свежее состояние: запрет смены снаряжения наступает после отправки `payload`."""
    return lambda: meeting(world.state) if payload in world.game.payloads() else world.state


def dump_window(world: World) -> tuple[StateFn, At]:
    """Битва через два часа с небольшим и часы за 10 минут до неё: окно слива (5 + 10 минут), но
    ещё не окно битвы (6 минут)."""
    battle = (datetime.now(UTC) + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    seen = Obs(value=battle, at=battle - timedelta(hours=1))

    def state() -> CharacterState:
        return world.state.model_copy(update={"battle_at": seen})

    return state, At(battle - timedelta(minutes=10))


@certifies("gadget_buy")
async def test_buy_and_wear(live_buying: World) -> None:
    shop(live_buying)
    live_buying.game.on_text("/buy_right1", game_text(BOUGHT_RIGHT1))
    live_buying.game.on_text("/inv", game_text(INV_BOUGHT))
    live_buying.game.on_text("/wear_11_p1", game_text(WEAR_P1))
    status, reason, details = await run(
        live_buying, rule="empty", slot="right", tier=1, price=3, reserve=0, wear=True
    )
    assert (status, reason) == ("done", "bought")
    assert details == {
        "bought": "Китайская мобила",
        "price": 3,
        "rule": "empty",
        "slot": "right",
        "tier": 1,
        "worn": True,
        "sold": [],
    }
    assert live_buying.game.payloads() == [*NAV, "/buy_right1", "/inv", "/wear_11_p1"]


@certifies("gadget_buy")
async def test_buy_without_wear(live_buying: World) -> None:
    shop(live_buying)
    live_buying.game.on_text("/buy_right1", game_text(BOUGHT_RIGHT1))
    status, reason, details = await run(
        live_buying, rule="set", slot="right", tier=1, price=3, reserve=0
    )
    assert (status, reason, details["worn"]) == ("done", "bought", False)
    assert live_buying.game.payloads() == [*NAV, "/buy_right1"]


@certifies("gadget_buy")
async def test_bought_name_compared_ignoring_case(live_buying: World) -> None:
    shop(live_buying)
    answer = BOUGHT_RIGHT1.replace("Китайская мобила", "китайская Мобила")
    assert answer != BOUGHT_RIGHT1
    live_buying.game.on_text("/buy_right1", game_text(answer))
    status, reason, _ = await run(
        live_buying, rule="set", slot="right", tier=1, price=3, reserve=0
    )
    assert (status, reason) == ("done", "bought")


@certifies("gadget_buy")
async def test_not_enough_answer_is_cant_afford(live_buying: World) -> None:
    # Витрина показала деньги на тир 14, а игра ответила «не хватает всего-то».
    shop(live_buying, with_money("59\xa0999"))
    live_buying.game.on_text("/buy_right14", game_text(NO_MONEY_RIGHT14))
    status, reason, _ = await run(
        live_buying, rule="set", slot="right", tier=14, price=59999, reserve=0
    )
    assert (status, reason) == ("nothing", "cant_afford")
    assert live_buying.game.payloads() == [*NAV, "/buy_right14"]


@certifies("gadget_buy")
async def test_showcase_mismatch_stops_before_selling(live_buying: World) -> None:
    shop(live_buying, SHOP_RIGHT.replace("$59\xa0999\n/buy_right14", "$64\xa0999\n/buy_right14"))
    live_buying.game.on_text("/stock", ("stocks", 3624065))
    status, reason, details = await run(
        live_buying, rule="set", slot="right", tier=14, price=59999, reserve=0
    )
    assert (status, reason) == ("nothing", "shop_mismatch")
    assert details == {
        "slot": "right",
        "tier": 14,
        "seen": {"name": "GiftPhone", "price": 64999, "level": 47},
    }
    assert live_buying.game.payloads() == NAV


def own_dearest() -> IncomingMessage:
    """Биржа 3624065, где своя ☣️ дороже всех продаваемых чужих (50 при лимите 80)."""
    msg = game_msg("stocks", 3624065)
    text = (msg.text or "").replace("☣️Black Mesa - 10 💵 за шт.", "☣️Black Mesa - 50 💵 за шт.")
    assert text != msg.text
    return replace(msg, text=text)


@certifies("gadget_buy")
@pytest.mark.parametrize(
    "stock", [("stocks", 3624065), own_dearest()], ids=["live", "own_dearest"]
)
async def test_sells_foreign_stock_then_reopens_showcase(live_buying: World, stock: Ref) -> None:
    # Витрина: $555 на Hooli phone за $4 449. Биржа (открыта): $2 364, лимит продажи $80; ☂️ по
    # 100 выше лимита, своя ☣️ не продаётся ни по 10, ни дороже всех (по 50). Чужие: ⚡️ по 31 и
    # 📯, 🤖, 🎩 по 10. Нехватка $2 085: ⚡️ вся (51 шт., +$1 530), остаток $555 — 📯
    # ⌈555 / 9⌉ = 62 шт.
    shop(live_buying)
    live_buying.game.on_text("📱Правая рука", game_text(with_money("4\xa0452")))
    live_buying.game.on_text("/stock", stock)
    live_buying.game.on_text("/sells_stark_51", sold("⚡️Stark Ind.", 31, 3894, 0, 51))
    live_buying.game.on_text("/sells_piper_62", sold("📯Pied Piper", 10, 4452, 3701, 62))
    bought = BOUGHT_RIGHT1.replace("Китайская мобила (+1🔨)", "Hooli phone (+17🔨, +7🎓)")
    live_buying.game.on_text("/buy_right8", game_text(bought))
    status, reason, details = await run(
        live_buying, rule="set", slot="right", tier=8, price=4449, reserve=0
    )
    assert (status, reason) == ("done", "bought")
    assert (details["bought"], details["price"], details["worn"]) == ("Hooli phone", 4449, False)
    assert details["sold"] == [
        {"company": "stark", "n": 51, "price": 31},
        {"company": "piper", "n": 62, "price": 10},
    ]
    assert live_buying.game.payloads() == [
        *NAV,
        "/stock",
        "/sells_stark_51",
        "/sells_piper_62",
        *NAV,
        "/buy_right8",
    ]


@certifies("gadget_buy")
async def test_whole_portfolio_short_sells_nothing(live_buying: World) -> None:
    # Тир 14 ($59 999): $2 364 и выручка за чужие акции до лимита за вычетом комиссии $1/шт. —
    # $52 614 (⚡️ 51 × 30, 📯 3 763 × 9, 🤖 1 838 × 9, 🎩 75 × 9) — не хватит.
    shop(live_buying)
    live_buying.game.on_text("/stock", ("stocks", 3624065))
    status, reason, _ = await run(
        live_buying, rule="set", slot="right", tier=14, price=59999, reserve=0
    )
    assert (status, reason) == ("nothing", "cant_afford")
    assert live_buying.game.payloads() == [*NAV, "/stock"]


@certifies("gadget_buy")
@pytest.mark.parametrize(
    ("tier", "price", "window", "reason", "tail"),
    [
        (8, 4449, True, "dump_window", []),
        (1, 3, True, "dump_window", []),
        (8, 4449, False, "market_closed", ["/stock"]),
    ],
)
async def test_dump_window_and_closed_market(
    live_buying: World, tier: int, price: int, window: bool, reason: str, tail: list[str]
) -> None:
    shop(live_buying)
    live_buying.game.on_text("/stock", ("stocks", 3624609))
    state, clock = dump_window(live_buying) if window else (None, None)
    status, got, _ = await run(
        live_buying,
        state=state,
        clock=clock,
        rule="set",
        slot="right",
        tier=tier,
        price=price,
        reserve=0,
    )
    assert (status, got) == ("nothing", reason)
    assert live_buying.game.payloads() == [*NAV, *tail]


@certifies("gadget_buy")
async def test_gear_window_before_buy(live_buying: World) -> None:
    shop(live_buying)
    status, reason, _ = await run(
        live_buying,
        state=lambda: meeting(live_buying.state),
        rule="empty",
        slot="right",
        tier=1,
        price=3,
        reserve=0,
        wear=True,
    )
    assert (status, reason) == ("nothing", "gorbushka_meeting")
    assert live_buying.game.payloads() == NAV


@certifies("gadget_buy")
async def test_gear_window_before_selling(live_buying: World) -> None:
    # Покупка в рюкзак с нехваткой наличных: окно-запрет — до продажи акций, а не после неё.
    shop(live_buying)
    live_buying.game.on_text("/stock", ("stocks", 3624065))
    status, reason, details = await run(
        live_buying,
        state=lambda: meeting(live_buying.state),
        rule="set",
        slot="right",
        tier=8,
        price=4449,
        reserve=0,
    )
    assert (status, reason, details) == ("nothing", "gorbushka_meeting", {})
    assert live_buying.game.payloads() == NAV


@certifies("gadget_buy")
async def test_wear_skipped_in_gear_window(live_buying: World) -> None:
    shop(live_buying)
    live_buying.game.on_text("/buy_right1", game_text(BOUGHT_RIGHT1))
    status, reason, details = await run(
        live_buying,
        state=guarded_after(live_buying, "/buy_right1"),
        rule="empty",
        slot="right",
        tier=1,
        price=3,
        reserve=0,
        wear=True,
    )
    assert (status, reason, details["worn"]) == ("done", "bought", False)
    assert live_buying.game.payloads() == [*NAV, "/buy_right1"]


@certifies("gadget_buy")
@pytest.mark.parametrize(
    ("lines", "sent"),
    [
        ((f"{P1_UPGRADED} /wear_12_p1", f"{P1} /wear_13_p1"), "/wear_13_p1"),
        ((f"{P1} /wear_12_p1", f"{P1_UPGRADED} /wear_13_p1"), "/wear_12_p1"),
    ],
    ids=["fresh_last", "upgraded_last"],
)
async def test_wear_picks_ungraded_of_same_code(
    live_buying: World, lines: tuple[str, str], sent: str
) -> None:
    shop(live_buying)
    live_buying.game.on_text("/buy_right1", game_text(BOUGHT_RIGHT1))
    live_buying.game.on_text("/inv", game_text(bag_of(f"{BLACKM} /wear_11_p18", *lines)))
    live_buying.game.on_text(sent, game_text(WEAR_P1))
    status, reason, details = await run(
        live_buying, rule="empty", slot="right", tier=1, price=3, reserve=0, wear=True
    )
    assert (status, reason, details["worn"]) == ("done", "bought", True)
    assert live_buying.game.payloads()[-2:] == ["/inv", sent]


@certifies("gadget_buy")
async def test_unknown_command_on_wear_retries_inv_once(live_buying: World) -> None:
    shop(live_buying)
    live_buying.game.on_text("/buy_right1", game_text(BOUGHT_RIGHT1))
    live_buying.game.on_text("/inv", game_text(INV_BOUGHT))
    live_buying.game.on_text("/wear_11_p1", game_text(UNKNOWN))
    live_buying.game.on_text("/wear_11_p1", game_text(WEAR_P1))
    status, reason, details = await run(
        live_buying, rule="empty", slot="right", tier=1, price=3, reserve=0, wear=True
    )
    assert (status, reason, details["worn"]) == ("done", "bought", True)
    assert live_buying.game.payloads() == [
        *NAV,
        "/buy_right1",
        "/inv",
        "/wear_11_p1",
        "/inv",
        "/wear_11_p1",
    ]


@certifies("gadget_buy")
async def test_retry_after_unknown_command_checks_gear_guard(live_buying: World) -> None:
    shop(live_buying)
    live_buying.game.on_text("/buy_right1", game_text(BOUGHT_RIGHT1))
    live_buying.game.on_text("/inv", game_text(INV_BOUGHT))
    live_buying.game.on_text("/wear_11_p1", game_text(UNKNOWN))
    status, reason, details = await run(
        live_buying,
        state=guarded_after(live_buying, "/wear_11_p1"),
        rule="empty",
        slot="right",
        tier=1,
        price=3,
        reserve=0,
        wear=True,
    )
    assert (status, reason) == ("done", "bought")
    assert (details["worn"], details["wear"]) == (False, "gorbushka_meeting")
    assert live_buying.game.payloads() == [*NAV, "/buy_right1", "/inv", "/wear_11_p1"]


@certifies("gadget_buy")
async def test_unknown_command_twice_after_buy_keeps_purchase(live_buying: World) -> None:
    shop(live_buying)
    live_buying.game.on_text("/buy_right1", game_text(BOUGHT_RIGHT1))
    live_buying.game.on_text("/inv", game_text(INV_BOUGHT))
    live_buying.game.on_text("/wear_11_p1", game_text(UNKNOWN))
    status, reason, details = await run(
        live_buying, rule="empty", slot="right", tier=1, price=3, reserve=0, wear=True
    )
    assert (status, reason) == ("done", "bought")
    assert (details["worn"], details["wear"]) == (False, "wrong_screen")
    assert live_buying.game.payloads()[-4:] == ["/inv", "/wear_11_p1", "/inv", "/wear_11_p1"]


@certifies("gadget_buy")
async def test_copy_in_bag_is_worn_without_shop(live_buying: World) -> None:
    live_buying.game.on_text("/inv", game_text(INV_BOUGHT))
    live_buying.game.on_text("/wear_11_p1", game_text(WEAR_P1))
    status, reason, details = await run(
        live_buying, rule="empty", slot="right", tier=1, price=0, reserve=0, wear=True, in_bag=True
    )
    assert (status, reason) == ("done", "worn")
    assert details == {
        "gadget": "Китайская мобила",
        "rule": "empty",
        "slot": "right",
        "tier": 1,
        "worn": True,
    }
    assert live_buying.game.payloads() == ["/inv", "/wear_11_p1"]


@certifies("gadget_buy")
@pytest.mark.parametrize(
    ("inv", "answers", "result", "sent"),
    [
        (bag_of(), [], ("nothing", "missing_item"), ["/inv"]),
        (INV_BOUGHT, [UNKNOWN], ("failed", "wrong_screen"), ["/inv", "/wear_11_p1"] * 2),
    ],
)
async def test_copy_in_bag_not_worn(
    live_buying: World, inv: str, answers: list[str], result: tuple[str, str], sent: list[str]
) -> None:
    live_buying.game.on_text("/inv", game_text(inv))
    for answer in answers:
        live_buying.game.on_text("/wear_11_p1", game_text(answer))
    status, reason, _ = await run(
        live_buying, rule="empty", slot="right", tier=1, price=0, reserve=0, wear=True, in_bag=True
    )
    assert (status, reason) == result
    assert live_buying.game.payloads() == sent


UM_PHONE = "📱Um-Phone (+37🔨, +19🎓)"
UM_WATCH = "⌚️Um-Watch (+37🎓, +19🔨)"
SETS_BEFORE = ["⚫️Сет VIP", "🗳Сет Логистик", "🗺Сет Кладоискатель", "🦉Сет Сова"]


@certifies("gadget_wear_set")
async def test_wear_set_remaining_parts_and_reports_sets(live_buying: World) -> None:
    # Номера в рюкзаке сдвигаются после надевания: часы — 12-е до, 11-е после.
    first = bag_of(f"{UM_PHONE} /wear_11_p13", f"{UM_WATCH} /wear_12_w13")
    second = bag_of(f"{UM_WATCH} /wear_11_w13", f"{BLACKM} /wear_12_p18")
    live_buying.game.on_text("/inv", game_text(first))
    live_buying.game.on_text("/inv", game_text(second))
    live_buying.game.on_text("/wear_11_p13", game_text(worn_answer(UM_PHONE)))
    live_buying.game.on_text("/wear_11_w13", game_text(worn_answer(UM_WATCH, "⚫️Сет VIP")))
    status, reason, details = await run_set(live_buying, set="um", slots=["right", "left"])
    assert (status, reason) == ("done", "worn")
    assert details == {
        "set": "um",
        "active": None,
        "sets_before": SETS_BEFORE,
        "sets": ["⚫️Сет VIP"],
    }
    assert live_buying.game.payloads() == ["/inv", "/wear_11_p13", "/inv", "/wear_11_w13"]


@certifies("gadget_wear_set")
@pytest.mark.parametrize(("tail", "active"), [("🌞Сет Летний", True), ("🗳Сет Логистик", False)])
async def test_wear_set_active_by_set_line(live_buying: World, tail: str, active: bool) -> None:
    s_mart = "📱S-март (+29🔨, +15🎓)"
    live_buying.game.on_text("/inv", game_text(bag_of(f"{s_mart} /wear_11_p11")))
    live_buying.game.on_text("/wear_11_p11", game_text(worn_answer(s_mart, tail)))
    status, reason, details = await run_set(live_buying, set="summer", slots=["right"])
    assert (status, reason, details["active"], details["sets"]) == ("done", "worn", active, [tail])


@certifies("gadget_wear_set")
async def test_wear_set_part_by_code_when_name_differs(live_buying: World) -> None:
    s_mart = "📱Samsung S-MART (+29🔨, +15🎓)"
    live_buying.game.on_text("/inv", game_text(bag_of(f"{s_mart} /wear_11_p11")))
    live_buying.game.on_text("/wear_11_p11", game_text(worn_answer(s_mart, "🌞Сет Летний")))
    status, reason, details = await run_set(live_buying, set="summer", slots=["right"])
    assert (status, reason, details["active"]) == ("done", "worn", True)
    assert live_buying.game.payloads() == ["/inv", "/wear_11_p11"]


@certifies("gadget_wear_set")
async def test_wear_set_missing_item(live_buying: World) -> None:
    live_buying.game.on_text("/inv", game_text(INV_BOUGHT))
    status, reason, _ = await run_set(live_buying, set="um", slots=["right"])
    assert (status, reason) == ("nothing", "missing_item")
    assert live_buying.game.payloads() == ["/inv"]


@certifies("gadget_wear_set")
async def test_wear_set_stops_on_gear_guard_between_slots(live_buying: World) -> None:
    first = bag_of(f"{UM_PHONE} /wear_11_p13", f"{UM_WATCH} /wear_12_w13")
    live_buying.game.on_text("/inv", game_text(first))
    live_buying.game.on_text("/wear_11_p13", game_text(worn_answer(UM_PHONE)))
    status, reason, _ = await run_set(
        live_buying,
        state=guarded_after(live_buying, "/wear_11_p13"),
        set="um",
        slots=["right", "left"],
    )
    assert (status, reason) == ("nothing", "gorbushka_meeting")
    assert live_buying.game.payloads() == ["/inv", "/wear_11_p13"]
