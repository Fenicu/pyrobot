from dataclasses import replace

import pytest

from app.engine.events import Event
from app.engine.parsing import default_parser
from app.engine.parsing.common import Rewards
from app.engine.parsing.gifts import TangerineGiftBought, TangerineGiftOpened, TangerineGiftShop
from app.engine.parsing.items import GiftsScreen
from app.engine.parsing.refusals import Busy, Refused
from app.engine.settings import ChatsSection
from tests.engine import gift_texts as g
from tests.fixtures import game_msg


def events(text: str, *counts: int) -> list[Event]:
    return default_parser(ChatsSection()).parse(g.gift_msg(text, buttons=g.options(*counts)))


def test_gifts_screen_reads_tangerine_gifts() -> None:
    assert events(g.GIFTS_WITH_TANGERINE_GIFTS) == [
        GiftsScreen(containers_small=3, containers_medium=0, tangerines=2, tangerine_gifts=1)
    ]


def test_gifts_screen_fixture_zero_tangerine_gifts() -> None:
    parsed = default_parser(ChatsSection()).parse(game_msg("items", 3623585))
    assert parsed == [
        GiftsScreen(containers_small=5, containers_medium=0, tangerines=2, tangerine_gifts=0)
    ]


def test_gifts_screen_without_tangerine_line() -> None:
    text = g.GIFTS_WITH_TANGERINE_GIFTS.replace("🎁Твои за 🍊: 1 шт.\n/unbox_t\n\n", "")
    assert events(text) == [
        GiftsScreen(containers_small=3, containers_medium=0, tangerines=2, tangerine_gifts=None)
    ]


def test_shop_picker() -> None:
    assert events(g.SHOP, *g.SHOP_OPTIONS) == [
        TangerineGiftShop(gifts=0, tangerines=237, options=g.SHOP_OPTIONS)
    ]


def test_shop_short_from_start() -> None:
    assert events(g.SHORT) == [TangerineGiftShop(gifts=0, tangerines=2, short=8)]


def test_shop_short_fixture() -> None:
    parsed = default_parser(ChatsSection()).parse(game_msg("screens", 3584848))
    assert parsed == [TangerineGiftShop(gifts=0, tangerines=2, short=8)]


def test_bought_with_more_options() -> None:
    assert events(g.BOUGHT, *g.BOUGHT_OPTIONS) == [
        TangerineGiftShop(gifts=3, tangerines=203, options=g.BOUGHT_OPTIONS),
        TangerineGiftBought(count=3, paid=30),
    ]


def test_bought_all() -> None:
    assert events(g.BOUGHT_ALL) == [
        TangerineGiftShop(gifts=4, tangerines=2, short=8),
        TangerineGiftBought(count=4, paid=40),
    ]


def test_options_sorted_and_foreign_buttons_ignored() -> None:
    msg = g.gift_msg(g.SHOP, buttons=g.options(23, 1, 5))
    extra = replace(msg.inline[0], data="cancel_inline", text="x")
    parsed = default_parser(ChatsSection()).parse(replace(msg, inline=(*msg.inline, extra)))
    assert parsed == [TangerineGiftShop(gifts=0, tangerines=237, options=(1, 5, 23))]


def test_opened_with_fastfood() -> None:
    assert events(g.OPENED) == [
        TangerineGiftOpened(
            rewards=Rewards(money=73, upgrades_white=2),
            food={"hotdog": 2, "pizza": 1, "burger": 1},
        )
    ]


def test_opened_pet_food_only() -> None:
    assert events(g.OPENED_PET_FOOD) == [TangerineGiftOpened(rewards=Rewards(money=79))]


def test_opened_fixture() -> None:
    parsed = default_parser(ChatsSection()).parse(game_msg("screens", 3550799))
    assert parsed == [
        TangerineGiftOpened(
            rewards=Rewards(money=73, upgrades_white=2),
            food={"hotdog": 2, "pizza": 1, "burger": 1},
        )
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [(g.NO_GIFT, Refused(reason="no_such_gift")), (g.BUSY, Busy(left_s=119))],
    ids=["no_gift", "busy"],
)
def test_open_refusals(text: str, expected: Event) -> None:
    assert events(text) == [expected]
