import pytest

from app.engine.events import Event
from app.engine.parsing.gorbushka import (
    GorbushkaFight,
    GorbushkaNotice,
    GorbushkaScreen,
    recognize_gorbushka,
)
from tests.fixtures import game_msg


def _events(msg_id: int) -> list[Event]:
    return recognize_gorbushka(game_msg("gorbushka", msg_id))


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (3516661, GorbushkaScreen(state="done", comeback_in_s=21600)),
        (
            3516741,
            GorbushkaScreen(
                state="waiting", won=1, total=4, ticket_left_s=82800, next_in_s=23, stamina=100
            ),
        ),
        (
            3516738,
            GorbushkaScreen(
                state="meeting",
                won=0,
                total=4,
                ticket_left_s=86340,
                fight_cost_motivation=1,
                stamina=100,
            ),
        ),
        (
            3528135,
            GorbushkaScreen(
                state="need_ticket",
                total=4,
                ticket_money=120,
                ticket_knowledge=20,
                money=53,
                knowledge=17721,
            ),
        ),
        (
            3537930,
            GorbushkaScreen(
                state="need_ticket",
                total=4,
                ticket_money=120,
                ticket_knowledge=20,
                money=1544,
                knowledge=17977,
            ),
        ),
        (
            3520526,
            GorbushkaScreen(
                state="need_ticket",
                total=4,
                ticket_money=120,
                ticket_knowledge=20,
                money=104,
                knowledge=17627,
                short_of=16,
            ),
        ),
        (3516793, GorbushkaNotice(notice="no_seller")),
        (3524271, GorbushkaNotice(notice="skills_changed")),
    ],
)
def test_screens(msg_id: int, expected: Event) -> None:
    assert _events(msg_id) == [expected]


def test_fights() -> None:
    won, box, torch, lost = (_events(i)[0] for i in (3516739, 3516744, 3518798, 3564182))
    assert isinstance(won, GorbushkaFight) and isinstance(lost, GorbushkaFight)
    assert isinstance(box, GorbushkaFight) and isinstance(torch, GorbushkaFight)
    assert (won.won, won.rewards.exp, won.rewards.details, won.rewards.prizebox) == (
        True,
        224,
        23,
        False,
    )
    assert (box.rewards.upgrades_blue, box.rewards.prizebox) == (1, True)
    assert (torch.rewards.upgrades_red, torch.rewards.prizebox) == (1, True)
    assert (lost.won, lost.rewards.exp) == (False, 0)
