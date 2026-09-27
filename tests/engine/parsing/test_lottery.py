from dataclasses import replace

import pytest

from app.engine.events import Event
from app.engine.parsing.lottery import (
    LotteryBought,
    LotteryCurrency,
    LotteryOff,
    LotteryScreen,
    recognize_lottery,
)
from app.engine.parsing.refusals import Refused, recognize_refusals
from tests.fixtures import game_msg, game_versions


def _events(msg_id: int) -> list[Event]:
    return recognize_lottery(game_msg("lottery", msg_id))


def _four(money: int, knowledge: int, raw: int, details: int) -> dict[str, int]:
    return {"money": money, "knowledge": knowledge, "raw": raw, "details": details}


def test_screen_with_limits_prices_and_resources() -> None:
    # Живой экран 26.09 19:20 — ответ на 🤑Лотерея.
    assert _events(3625282) == [
        LotteryScreen(
            draw=3285,
            draw_in_s=6960,
            bought=_four(0, 0, 0, 0),
            limits=_four(10, 7, 7, 7),
            prices=_four(30, 4, 4, 8),
            resources=_four(675, 21951, 21324, 136669),
        )
    ]


def test_old_screen_without_vs16_and_partly_bought() -> None:
    assert _events(1624368) == [
        LotteryScreen(
            draw=802,
            draw_in_s=5160,
            bought=_four(3, 7, 7, 7),
            limits=_four(10, 7, 7, 7),
            prices=_four(30, 4, 4, 8),
            resources=_four(31, 2940, 1047, 16521),
        )
    ]


@pytest.mark.parametrize(
    ("msg_id", "draw", "draw_in_s", "bought"),
    [
        # «Купить все» 26.09: всё до лимита 10/7/7/7.
        (3625321, 3285, 4560, _four(10, 7, 7, 7)),
        # Февраль 2026: лимиты 13/10/10/10, на 💵 денег хватило на 7.
        (3536223, 3063, 7140, _four(7, 10, 10, 10)),
        # Вторая покупка тиража 802: этой командой — один билет за 💵 (всего стало 4 из 10).
        (1624370, 802, 5100, _four(1, 0, 0, 0)),
        # Повторное «купить все» того же тиража: куплено 0.
        (3525365, 3041, 7140, _four(0, 0, 0, 0)),
    ],
)
def test_buy_all_answer_is_what_this_command_bought(
    msg_id: int, draw: int, draw_in_s: int, bought: dict[str, int]
) -> None:
    assert _events(msg_id) == [LotteryBought(draw=draw, draw_in_s=draw_in_s, bought=bought)]


@pytest.mark.parametrize(("msg_id", "bought", "limit"), [(3385821, 10, 10), (3402013, 4, 13)])
def test_currency_screen_short_of_money(msg_id: int, bought: int, limit: int) -> None:
    [event] = _events(msg_id)
    assert isinstance(event, LotteryCurrency)
    assert (event.currency, event.bought, event.limit, event.price, event.short) == (
        "money",
        bought,
        limit,
        30,
        True,
    )


def test_live_draw_3286_screens() -> None:
    # Живой съём 27.09: экран до покупки и после «купить все».
    [before] = _events(3626217)
    [after] = _events(3626223)
    assert isinstance(before, LotteryScreen) and isinstance(after, LotteryScreen)
    assert (before.draw, before.bought, before.resources["money"]) == (
        3286,
        _four(0, 0, 0, 0),
        4785,
    )
    assert (after.bought, after.resources) == (
        _four(10, 7, 7, 7),
        _four(4485, 21945, 21572, 136611),
    )
    # «Купить все» после одного билета за 💵: этой командой — 9 за 💵.
    assert _events(3626221) == [LotteryBought(draw=3286, draw_in_s=6960, bought=_four(9, 7, 7, 7))]


def test_currency_screen_and_its_edit_after_click() -> None:
    opened, clicked = (recognize_lottery(m) for m in game_versions("lottery", 3626219))
    assert opened == [
        LotteryCurrency(currency="money", draw_in_s=6960, bought=0, limit=10, price=30)
    ]
    assert clicked == [
        LotteryCurrency(currency="money", draw_in_s=6960, bought=1, limit=10, price=30)
    ]
    frames = game_versions("lottery", 3626219)
    assert [b.data for b in frames[1].inline] == [
        "tickets_money_1",
        "tickets_money_3",
        "tickets_money_5",
        "tickets_money_7",
        "tickets_money_9",
        "cancel_inline",
    ]


def test_currency_on_limit() -> None:
    assert _events(3626225) == [
        LotteryCurrency(
            currency="knowledge", draw_in_s=6900, bought=7, limit=7, price=4, full=True
        )
    ]
    assert game_msg("lottery", 3626225).inline == ()


def test_off_and_closed() -> None:
    assert _events(3624975) == [LotteryOff()]
    closed = game_msg("lottery", 1640401)
    assert recognize_lottery(closed) == []
    assert recognize_refusals(closed) == [Refused(reason="lottery_closed")]


def test_partial_screen_gives_nothing() -> None:
    msg = game_msg("lottery", 3625282)
    assert msg.text is not None
    no_price = replace(msg, text=msg.text.replace("⚙️8 за шт.\n", ""))
    lines = [line for line in msg.text.split("\n") if not line.startswith("🔩Сырьё:")]
    no_resources = replace(msg, text="\n".join(lines))
    assert recognize_lottery(no_price) == recognize_lottery(no_resources) == []


def test_buy_all_total_must_match_lines() -> None:
    msg = game_msg("lottery", 3625321)
    assert msg.text is not None
    broken = replace(msg, text=msg.text.replace("Всего: 31 шт.", "Всего: 30 шт."))
    assert recognize_lottery(broken) == []
