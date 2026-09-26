from dataclasses import replace

import pytest

from app.engine.events import Event
from app.engine.parsing.activities import (
    RECOGNIZERS,
    ActivityCancelled,
    ActivityFinished,
    ActivityStarted,
    BonusRewards,
    DeedsMenu,
    MotivationFull,
    Price,
    PricesScreen,
    WorkshopScreen,
)
from tests.fixtures import game_msg


def _events(msg_id: int) -> list[Event]:
    msg = game_msg("activities", msg_id)
    return [e for recognize in RECOGNIZERS for e in recognize(msg)]


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (3517276, ActivityStarted(activity="harvest", duration_s=300, money=30)),
        (3517898, ActivityStarted(activity="job", duration_s=145)),
        (3623881, ActivityStarted(activity="job", duration_s=120)),
        (3603614, ActivityStarted(activity="learn", duration_s=210)),
        (3516647, ActivityStarted(activity="eat", duration_s=300, money=5)),
        (3624728, ActivityStarted(activity="dconv", duration_s=360, money=5, details=10)),
    ],
)
def test_starts(msg_id: int, expected: ActivityStarted) -> None:
    assert _events(msg_id) == [expected]


def _finished(msg_id: int) -> ActivityFinished:
    events = _events(msg_id)
    assert len(events) == 1 and isinstance(events[0], ActivityFinished)
    return events[0]


def test_harvest_results() -> None:
    assert (_finished(3517279).activity, _finished(3517279).rewards.exp) == ("harvest", 158)
    assert _finished(3610659).rewards.exp == 348
    assert _finished(3610665).rewards.exp == 233
    assert (_finished(3517344).failed, _finished(3518414).failed) == (True, True)
    assert _finished(3517279).failed is False


def test_other_results() -> None:
    job = _finished(3517901)
    assert (job.activity, job.rewards.money, job.rewards.details) == ("job", 28, 4)
    learn = _finished(3603617)
    assert (learn.activity, learn.rewards.knowledge) == ("learn", 9)
    assert [_finished(i).rewards.stamina for i in (3516648, 3517759, 3520195)] == [100, 0, 200]
    dconv = _finished(3624873)
    assert (dconv.activity, dconv.rewards.exp, dconv.rewards.raw) == ("dconv", 253, 5)


def test_cancel_variants() -> None:
    assert _events(3517963) == [ActivityCancelled(result="ok", motivation=1)]
    assert _events(3522301) == [ActivityCancelled(result="ok", money=5)]
    assert _events(3517930) == [ActivityCancelled(result="too_late")]
    assert _events(3529038) == [ActivityCancelled(result="nothing")]


def test_motivation_full_and_magnet() -> None:
    assert _events(3517795) == [MotivationFull()]
    magnet = _events(3524596)
    assert len(magnet) == 1 and isinstance(magnet[0], BonusRewards)
    assert (magnet[0].source, magnet[0].rewards.exp) == ("magnet", 247)


def test_deeds_menu_compact_and_full() -> None:
    deeds = {
        "job": Price(motivation=1, minutes=5),
        "walk": Price(motivation=1, minutes=5),
        "rob": Price(motivation=1, minutes=8),
    }
    assert _events(3610643) == [
        PricesScreen(screen="deeds", prices=deeds),
        DeedsMenu(stamina=100, sleep_in_s=3360, sleeping=None, sleeping_left_s=None),
    ]
    assert _events(3548692)[1] == DeedsMenu(
        stamina=100, sleep_in_s=None, sleeping="hotel", sleeping_left_s=25140
    )
    assert _events(3516791) == [
        PricesScreen(screen="deeds", prices=deeds),
        DeedsMenu(stamina=100, sleep_in_s=118800, sleeping=None, sleeping_left_s=None),
    ]


def test_startup_workshop_profession_prices() -> None:
    assert _events(3624645) == [
        PricesScreen(
            screen="startup",
            prices={
                "learn": Price(motivation=2, minutes=7),
                "confa": Price(motivation=3, money=7, minutes=8),
            },
        )
    ]
    assert _events(3624750) == [
        PricesScreen(
            screen="workshop",
            prices={
                "dconv": Price(motivation=1, money=5, minutes=6, details=10),
                "white_to_blue": Price(motivation=1, money=5, minutes=6, white=3),
                "blue_to_red": Price(motivation=2, money=10, minutes=6, blue=3),
            },
        ),
        WorkshopScreen(
            money=862,
            raw=21310,
            details=136661,
            upgrades_white=10243,
            upgrades_blue=4797,
            upgrades_red=2525,
        ),
    ]
    assert _events(3517254) == [
        PricesScreen(
            screen="profession", prices={"harvest": Price(motivation=1, money=30, minutes=10)}
        )
    ]


@pytest.mark.parametrize(
    ("msg_id", "drop"),
    [
        (3610643, "🔫Грабить — 1🔥, 8 ⏰"),
        (3610643, "🔋Выносливость: 100%"),
        (3624645, "Требования: 3🔥, 7 💵, 8 ⏰"),
        (3624750, "⚪️ простые:"),
        (3624750, "🔵 → 🔴 - создать уникальные улучшения из редких."),
    ],
)
def test_partial_screen_gives_nothing(msg_id: int, drop: str) -> None:
    msg = game_msg("activities", msg_id)
    assert msg.text is not None and drop in msg.text
    broken = replace(msg, text=msg.text.replace(drop, "…"))
    assert [e for recognize in RECOGNIZERS for e in recognize(broken)] == []
