import json

from app.engine.events import AntiFlood, Unrecognized
from app.engine.parsing import default_parser
from app.engine.settings import ChatsSection
from tests.fixtures import game

FAMILIES = (
    "profile",
    "battle",
    "activities",
    "refusals",
    "sleep",
    "food",
    "items",
    "gorbushka",
    "levelup",
)


def test_every_fixture_recognized_and_json_safe() -> None:
    parser = default_parser(ChatsSection())
    for family in FAMILIES:
        for msg_id, msg in game(family).items():
            events = parser.parse(msg)
            assert events, (family, msg_id)
            assert not any(isinstance(e, Unrecognized) for e in events), (family, msg_id)
            for event in events:
                json.dumps(event.to_json(), ensure_ascii=False)


def test_one_recognizer_per_message() -> None:
    parser = default_parser(ChatsSection())
    for family in FAMILIES:
        for msg_id, msg in game(family).items():
            kinds = [e.kind for e in parser.parse(msg)]
            # Экраны «⏳Дела» дают цены и меню одновременно, прочие — одно событие.
            assert (
                len(kinds) == 1
                or kinds == ["prices_screen", "deeds_menu"]
                or kinds
                == [
                    "prices_screen",
                    "workshop_screen",
                ]
            ), (family, msg_id, kinds)


def test_antiflood_in_registry() -> None:
    events = default_parser(ChatsSection()).parse(game("refusals")[3518804])
    assert events == [AntiFlood()]
