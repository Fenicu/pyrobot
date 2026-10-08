import json

from app.engine.events import AntiFlood, Unrecognized
from app.engine.parsing import default_parser
from app.engine.settings import ChatsSection
from app.engine.types import Button
from tests.engine import daily_texts as d
from tests.engine import trip_texts as t
from tests.engine.artifact_texts import game_text
from tests.engine.parsing.test_activities import (
    DCONV_DOG,
    DECLINED,
    HARVEST_DOG,
    JOB_SHORT,
    LEARN_LIGHT,
)
from tests.engine.parsing.test_bulls import FIGHT_BUTTONS, WALK_BEAR, WALK_BULL
from tests.engine.parsing.test_tangerine import GIFTS
from tests.engine.scenarios.test_deeds import FULL_PROFILE
from tests.fixtures import game

# Посты канала смузи в семействе smoothie разбираются только при заданном канале.
CHATS = ChatsSection(smoothie_channel_id=-1001356300612)
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
    "crew",
    "bulls",
    "stocks",
    "smoothie",
    "tangerine",
    "lottery",
    "screens",
    "swinfo",
)


def test_every_fixture_recognized_and_json_safe() -> None:
    parser = default_parser(CHATS)
    for family in FAMILIES:
        for msg_id, msg in game(family).items():
            events = parser.parse(msg)
            assert events, (family, msg_id)
            assert not any(isinstance(e, Unrecognized) for e in events), (family, msg_id)
            for event in events:
                json.dumps(event.to_json(), ensure_ascii=False)


def test_one_recognizer_per_message() -> None:
    parser = default_parser(CHATS)
    for family in FAMILIES:
        for msg_id, msg in game(family).items():
            kinds = [e.kind for e in parser.parse(msg)]
            # Экраны «⏳Дела» дают цены и меню одновременно, прочие — одно событие.
            assert (
                len(kinds) == 1
                or kinds == ["prices_screen", "deeds_menu"]
                or kinds == ["prices_screen", "workshop_screen"]
                or kinds == ["prices_screen", "startup_screen"]
            ), (family, msg_id, kinds)


def test_antiflood_in_registry() -> None:
    events = default_parser(ChatsSection()).parse(game("refusals")[3518804])
    assert events == [AntiFlood()]


# Живые сообщения 03–05.10.2026, ушедшие на проде в нераспознанное: каждое — хотя бы одно событие
# без Unrecognized (у подтверждения командного задания — с его кнопками).
def _live_texts() -> list[tuple[str, tuple[Button, ...]]]:
    return [
        (FULL_PROFILE, ()),
        (d.LEADER_OFFERS, ()),
        (d.TEAM_CONFIRM, d.TEAM_CONFIRM_BUTTONS),
        (d.TEAM_CHOSEN, ()),
        (t.SCREEN, ()),
        (t.START_BIKE, ()),
        (t.RESULT_BIKE, ()),
        (t.RESULT_NOTHING_CAR, ()),
        (t.RESULT_TRAM_RAILS, ()),
        (t.RESULT_TRAM_GRANNIES, ()),
        (HARVEST_DOG, ()),
        (DCONV_DOG, ()),
        (JOB_SHORT, ()),
        (LEARN_LIGHT, ()),
        (DECLINED, ()),
        (WALK_BULL, FIGHT_BUTTONS),
        (WALK_BEAR, FIGHT_BUTTONS),
        *((text, ()) for text, _, _ in GIFTS),
    ]


def test_live_texts_of_october_recognized() -> None:
    parser = default_parser(ChatsSection())
    for text, buttons in _live_texts():
        events = parser.parse(game_text(text, buttons=buttons))
        assert events, text[:60]
        assert not any(isinstance(e, Unrecognized) for e in events), text[:60]
