import json
from dataclasses import asdict

import pytest

from app.engine.parsing.common import Rewards, dur, first_line, num, parse_rewards
from tests.fixtures import game_msg


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("10ч. 14 мин.", 36840),
        ("1 мин. 34 сек.", 94),
        ("27 сек.", 27),
        ("пару сек.", 2),
        ("5 минут", 300),
        ("2д. 19ч.", 241200),
        ("6ч.", 21600),
        ("9д. 16ч.", 835200),
        ("на 7 часов", 25200),
        ("58 мин.", 3480),
    ],
)
def test_dur(text: str, seconds: int) -> None:
    assert dur(text) == seconds


def test_num_strips_separators() -> None:
    assert num("17\xa0173\xa0536") == 17173536
    assert num("1 470") == 1470


def test_first_line_trimmed() -> None:
    assert first_line("a\nb") == "a"
    assert len(first_line("x" * 500)) == 200


def _rewards(family: str, msg_id: int) -> Rewards:
    text = game_msg(family, msg_id).text
    assert text is not None
    return parse_rewards(text)


def test_rewards_owl_bonus_summed() -> None:
    assert _rewards("activities", 3610659).exp == 348


def test_rewards_job() -> None:
    r = _rewards("activities", 3517901)
    assert (r.exp, r.money, r.details, r.raw, r.stamina) == (83, 28, 4, 2, None)


def test_rewards_team_task_and_knowledge() -> None:
    r = _rewards("activities", 3603617)
    assert (r.exp, r.knowledge, r.team_task) == (261, 9, (355, 360, "📚"))


def test_rewards_gorbushka_vip_details_upgrades_prizebox() -> None:
    r = _rewards("gorbushka", 3516744)
    assert (r.exp, r.money, r.knowledge, r.details, r.stamina) == (259, 36, 7, 16, 100)
    assert (r.upgrades_blue, r.prizebox) == (1, True)
    assert _rewards("gorbushka", 3516739).details == 23


def test_rewards_negative_and_unsigned_money() -> None:
    lost = _rewards("sleep", 3520076)
    assert (lost.money, lost.stamina) == (-4, 0)
    won = _rewards("sleep", 3568265)
    assert (won.money, won.stamina) == (41, 259)


def test_rewards_json_safe() -> None:
    json.dumps(asdict(_rewards("gorbushka", 3516744)))


@pytest.mark.parametrize(
    ("text", "tier", "n"),
    [
        ("⚪️ Улучшения: +2", "upgrades_white", 2),
        ("⚪️ Простые улучшения: +2 шт.", "upgrades_white", 2),
        ("🔵Редкие улучшения: +1 шт.", "upgrades_blue", 1),
    ],
)
def test_rewards_upgrade_formats(text: str, tier: str, n: int) -> None:
    assert getattr(parse_rewards(text), tier) == n
