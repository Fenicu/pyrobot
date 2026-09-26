import pytest

from app.engine.events import Event
from app.engine.parsing.crew import (
    CrewScreen,
    FactoryReport,
    FactoryScreen,
    FactorySignup,
    recognize_crew,
)
from tests.fixtures import game_msg


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (3624389, CrewScreen(tag="SU", factory_wins=247, signup_open=True)),
        (3624391, FactoryScreen(status="not_signed")),
        (3572473, FactoryScreen(status="signed")),
        (3586815, FactoryScreen(status="closed")),
        (3624393, FactorySignup(result="signed")),
        (3572475, FactorySignup(result="already")),
        (3621811, FactorySignup(result="skip")),
        (3620025, FactoryReport(won=True)),
        (3521460, FactoryReport(won=False)),
    ],
)
def test_crew_and_factory(msg_id: int, expected: Event) -> None:
    assert recognize_crew(game_msg("crew", msg_id)) == [expected]
