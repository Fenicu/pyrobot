import pytest

from app.engine.events import Event
from app.engine.parsing.sleep import (
    Acknowledged,
    FellAsleep,
    RobberyFight,
    SleepMenu,
    SleepWarning,
    WokeUp,
    recognize_sleep,
)
from tests.fixtures import game_msg


def _events(msg_id: int) -> list[Event]:
    return recognize_sleep(game_msg("sleep", msg_id))


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (3517243, SleepWarning(forced_in_s=7200)),
        (3517441, FellAsleep(where="bridge", hours=12, forced=True)),
        (3526861, SleepMenu(hotel_cost=210, short_of=None)),
        (3541940, SleepMenu(hotel_cost=213, short_of=88)),
        (3525189, FellAsleep(where="hotel", hours=7, cost=210)),
        (3541942, FellAsleep(where="bridge", hours=7)),
        (3525190, Acknowledged(topic="hotel_ack")),
        (3541943, Acknowledged(topic="bridge_ack")),
    ],
)
def test_sleep_messages(msg_id: int, expected: Event) -> None:
    assert _events(msg_id) == [expected]


def test_wake_up() -> None:
    bridge, hotel = _events(3517730)[0], _events(3525209)[0]
    assert isinstance(bridge, WokeUp) and isinstance(hotel, WokeUp)
    assert (bridge.where, bridge.rewards.exp, bridge.rewards.stamina) == ("bridge", 135, 100)
    assert (hotel.where, hotel.rewards.exp, hotel.rewards.stamina) == ("hotel", 131, 150)


def test_robbery_fight() -> None:
    lost, won = _events(3520076)[0], _events(3568265)[0]
    assert isinstance(lost, RobberyFight) and isinstance(won, RobberyFight)
    assert (lost.won, lost.robber_level, lost.rewards.money, lost.rewards.stamina) == (
        False,
        73,
        -4,
        0,
    )
    assert (won.won, won.robber, won.rewards.money, won.rewards.stamina) == (
        True,
        "☂️azarat",
        41,
        259,
    )
