from dataclasses import replace

import pytest

from app.engine.events import Event
from app.engine.parsing.common import Rewards
from app.engine.parsing.screens import (
    BattleMenu,
    BattleReport,
    DeedFinishedInstantly,
    EtherScreen,
    InfoScreen,
    LotteryScreen,
    LotterySkillsExpired,
    LotteryWin,
    ResourcesChanged,
    TopWorker,
    recognize_screens,
)
from tests.fixtures import game_msg


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (3622907, TopWorker(place=1)),
        (3613862, BattleMenu(battle_in_s=4 * 3600 + 13 * 60)),
        (3613861, BattleReport(hour=22)),
        (3516697, LotteryScreen(screen="tickets")),
        (3610934, InfoScreen(name="pets")),
        (3559757, InfoScreen(name="pets")),
        (3624063, InfoScreen(name="network")),
        (3615665, InfoScreen(name="market")),
        (3584848, InfoScreen(name="gifts_shop")),
        (3568618, InfoScreen(name="artifacts")),
        (3603396, InfoScreen(name="tops")),
        (3585192, InfoScreen(name="viruses")),
        (3623175, InfoScreen(name="office")),
        (3621873, InfoScreen(name="help")),
        (3606025, InfoScreen(name="casino")),
        (3607774, InfoScreen(name="bonuses")),
        (3525610, InfoScreen(name="gadgets", money=3250)),
        (3568823, InfoScreen(name="gadgets", money=445)),
        (3541473, InfoScreen(name="account")),
        (3569098, InfoScreen(name="full_profile")),
    ],
)
def test_screens(msg_id: int, expected: Event) -> None:
    assert recognize_screens(game_msg("screens", msg_id)) == [expected]


def test_gadgets_screen_without_sale_line_has_no_money() -> None:
    msg = game_msg("screens", 3525610)
    assert msg.text is not None
    tail = (
        "\n\n👍Ты продал б/у Простой ноут (+10🎓, +10🐿) и получил $2\xa0400\xa0💵"
        " от нового счастливого обладателя.\nТеперь у тебя $3\xa0250\xa0💵 на счету."
    )
    assert msg.text.endswith(tail)
    no_sale = replace(msg, text=msg.text.removesuffix(tail))
    assert recognize_screens(no_sale) == [InfoScreen(name="gadgets", money=None)]


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (
            3540652,
            ResourcesChanged(
                source="symbol_exchange", rewards=Rewards(money=516, upgrades_white=2)
            ),
        ),
        (
            3550799,
            ResourcesChanged(source="tangerine_gift", rewards=Rewards(money=73, upgrades_white=2)),
        ),
        (
            3528604,
            ResourcesChanged(
                source="shark",
                rewards=Rewards(money=-89, knowledge=-2, details=-4, raw=-2, stamina=0),
            ),
        ),
        (3545091, LotterySkillsExpired(skills=("theory",), rewards=Rewards(exp=142))),
        (3606840, EtherScreen(money=130)),
        (3593341, EtherScreen(money=796)),
        (3603799, DeedFinishedInstantly()),
    ],
)
def test_results_change_state(msg_id: int, expected: Event) -> None:
    assert recognize_screens(game_msg("screens", msg_id)) == [expected]


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (3533922, LotteryWin(rewards=Rewards(details=100))),
        (3528792, LotteryWin(rewards=Rewards(knowledge=100))),
        (3535553, LotteryWin(rewards=Rewards(raw=100))),
        (3520541, LotteryWin(rewards=Rewards(), motivation=10)),
        (3531641, LotteryWin(rewards=Rewards(), skills={"theory": 1})),
        (
            3533470,
            LotteryWin(rewards=Rewards(knowledge=100), containers_small=1, skills={"theory": 1}),
        ),
    ],
)
def test_lottery_prizes(msg_id: int, expected: Event) -> None:
    assert recognize_screens(game_msg("screens", msg_id)) == [expected]
