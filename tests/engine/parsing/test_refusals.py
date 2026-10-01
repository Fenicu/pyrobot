from dataclasses import replace

import pytest

from app.engine.parsing.refusals import Busy, Refused, recognize_refusals
from tests.fixtures import game_msg


@pytest.mark.parametrize(
    ("msg_id", "left"),
    [(3517617, 36840), (3517360, 94), (3517857, 27), (3548674, 2), (3604198, 40440)],
)
def test_busy(msg_id: int, left: int) -> None:
    assert recognize_refusals(game_msg("refusals", msg_id)) == [Busy(left_s=left)]


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (3518565, Refused(reason="no_motivation")),
        (3517896, Refused(reason="no_money", need=16)),
        (3520502, Refused(reason="battle_soon")),
        (3520192, Refused(reason="battle_running")),
        (3518816, Refused(reason="factory_running")),
        (3517440, Refused(reason="tired")),
        (3548677, Refused(reason="tired")),
        (3516893, Refused(reason="something_wrong")),
        (3577823, Refused(reason="card_cooldown", left_s=2940)),
        (3519079, Refused(reason="not_allowed")),
        (3521846, Refused(reason="fastfood_while_eating")),
        (3590672, Refused(reason="eat_while_sleeping")),
        (3520789, Refused(reason="prizebox_locked", left_s=17400)),
        (3535591, Refused(reason="no_such_gift")),
        (3527305, Refused(reason="unknown_command")),
        # «⏳Задания» не из меню команды и «/t_…» не с экрана заданий — та же общая справка.
        (3625754, Refused(reason="unknown_command")),
        (3625756, Refused(reason="unknown_command")),
        (3624999, Refused(reason="fastfood_cooldown", left_s=1740)),
        (3532814, Refused(reason="levelup_required")),
    ],
)
def test_refusals(msg_id: int, expected: Refused) -> None:
    assert recognize_refusals(game_msg("refusals", msg_id)) == [expected]


def test_antiflood_not_a_refusal() -> None:
    assert recognize_refusals(game_msg("refusals", 3518804)) == []


def test_harvest_without_profession() -> None:
    text = (
        "❌Ты не можешь отправиться за ресурсами. "
        "Добывать ресурсы могут только Барахольщик или Старьёвщик"
    )
    msg = replace(game_msg("refusals", 3518565), text=text, inline=())
    assert recognize_refusals(msg) == [Refused(reason="not_harvester")]
