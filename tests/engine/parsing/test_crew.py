from dataclasses import replace
from datetime import date

import pytest

from app.engine.events import Event
from app.engine.parsing.common import Rewards
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
        (3624389, CrewScreen(tag="SU", factory_wins=247, signup_open=True, glory=45090)),
        (3626163, CrewScreen(tag="SU", factory_wins=247, signup_open=True, glory=45180)),
        (3624391, FactoryScreen(status="not_signed")),
        (3572473, FactoryScreen(status="signed")),
        (3586815, FactoryScreen(status="closed")),
        (3624393, FactorySignup(result="signed")),
        (3572475, FactorySignup(result="already")),
        (3621811, FactorySignup(result="skip")),
    ],
)
def test_crew_and_factory(msg_id: int, expected: Event) -> None:
    assert recognize_crew(game_msg("crew", msg_id)) == [expected]


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (
            3620025,
            FactoryReport(
                won=True,
                day="2026-09-09",
                rewards=Rewards(
                    exp=281,
                    money=347,
                    details=10,
                    stamina=40,
                    upgrades_white=2,
                    upgrades_blue=1,
                    upgrades_red=1,
                ),
                treasury=145,
                rage=None,
            ),
        ),
        (
            3586813,
            FactoryReport(
                won=False,
                day="2026-06-16",
                rewards=Rewards(exp=94, money=615, stamina=0, upgrades_white=2),
                treasury=614,
                rage=10,
            ),
        ),
        (
            3521460,
            FactoryReport(
                won=False,
                day="2026-01-15",
                rewards=Rewards(exp=114, money=156, stamina=0, upgrades_white=2),
                treasury=156,
                rage=10,
            ),
        ),
        (
            3625108,
            FactoryReport(
                won=False, day="2026-09-25", rewards=Rewards(stamina=0), treasury=0, rage=10
            ),
        ),
    ],
)
def test_factory_report_rewards(msg_id: int, expected: FactoryReport) -> None:
    assert recognize_crew(game_msg("crew", msg_id)) == [expected]


# Отчёт чужого игрока от пользователя (28.09): формат наград тот же.
KAA = (
    "☣️[ST] Kaa (66)\n🔨475 🎓448 🐿340 🐢315\nБитва за фабрику 28.09.26: @startupwarsreport\n\n"
    "Ты прошёл первый раунд и одолел 1🐻.\n\nУвы, но во втором раунде ты не смог одолеть никого и "
    "твоя команда проиграла. Повезёт завтра!\n\n😡Твоя Ярость: 2 (1)\n\n💡Опыт: 73\n"
    "💵Деньги: +$63\n💵В казну: +$62\n⚪️Простые: +3\n🔋Осталось выносливости: 0% /to_eat"
)


def test_factory_report_of_sample() -> None:
    [report] = recognize_crew(replace(game_msg("crew", 3521460), text=KAA))
    assert report == FactoryReport(
        won=False,
        day="2026-09-28",
        rewards=Rewards(exp=73, money=63, stamina=0, upgrades_white=3),
        treasury=62,
        rage=2,
    )
    assert isinstance(report, FactoryReport) and report.battle_day == date(2026, 9, 28)
