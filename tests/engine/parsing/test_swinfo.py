from dataclasses import replace

import pytest

from app.engine.events import Event
from app.engine.parsing import default_parser
from app.engine.parsing.swinfo import (
    BattleSummary,
    CompanyDayRating,
    FactoryCall,
    FactoryResult,
    LotteryPost,
    recognize_swinfo,
)
from app.engine.settings import ChatsSection
from tests.fixtures import game_msg

PRICES = {"piper": 10, "hooli": 10, "stark": 31, "umbrl": 100, "wayne": 10, "bmesa": 10}


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (3817108, FactoryCall()),
        (3817112, FactoryResult(winner="ST")),
        (3816957, FactoryResult(winner="SU")),
        (3817122, BattleSummary(prices=PRICES)),
        (3817131, BattleSummary(prices=PRICES)),
        (3812953, LotteryPost(stage="prizes", draw=3096)),
        (3817132, CompanyDayRating()),
    ],
)
def test_swinfo_posts(msg_id: int, expected: Event) -> None:
    assert default_parser(ChatsSection()).parse(game_msg("swinfo", msg_id)) == [expected]


def test_other_senders_in_swinfo_chat_ignored() -> None:
    post = replace(game_msg("swinfo", 3817108), from_id=267519921)
    assert default_parser(ChatsSection()).parse(post) == []


def test_factory_winner_tag_after_company_emoji() -> None:
    # Тег с подчёркиванием: старый символьный класс его бы обрезал.
    msg = game_msg("swinfo", 3816957)
    assert msg.text is not None
    tagged = replace(msg, text=msg.text.replace("☣️SU (1)", "☣️S_U (1)"))
    assert recognize_swinfo(tagged) == [FactoryResult(winner="S_U")]
