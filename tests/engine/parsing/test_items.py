from dataclasses import replace

import pytest

from app.engine.events import Event
from app.engine.parsing.items import (
    BookRead,
    CardUsed,
    ContainerOpened,
    GiftsScreen,
    Inventory,
    PrizeboxOpened,
    recognize_items,
)
from tests.fixtures import game_msg


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (
            3516676,
            Inventory(books=613, cards=62, prizebox=False, prizebox_in_s=None, bag=10, bag_cap=24),
        ),
        (
            3625102,
            Inventory(books=875, cards=13, prizebox=True, prizebox_in_s=60480, bag=11, bag_cap=24),
        ),
        (3516680, BookRead(exp=457, next_in_s=3000)),
        (3516678, CardUsed(money=597, next_in_s=3000)),
        (3516682, GiftsScreen(containers_small=0, containers_medium=0, tangerines=2)),
        (3623585, GiftsScreen(containers_small=5, containers_medium=0, tangerines=2)),
        (3517971, ContainerOpened(size="small")),
        (3517262, PrizeboxOpened(money_after=1108)),
    ],
)
def test_items(msg_id: int, expected: Event) -> None:
    assert recognize_items(game_msg("items", msg_id)) == [expected]


def test_inventory_without_books_line_gives_nothing() -> None:
    msg = game_msg("items", 3625102)
    assert msg.text is not None
    broken = replace(msg, text=msg.text.replace("📒Книга опыта: 875 /read_exp", "…"))
    assert recognize_items(broken) == []
