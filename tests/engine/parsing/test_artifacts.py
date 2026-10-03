import pytest

from app.engine.events import Event
from app.engine.parsing import default_parser
from app.engine.parsing.activities import ActivityFinished
from app.engine.parsing.artifacts import (
    ArtifactCollectStarted,
    ArtifactPartFound,
    ArtifactsScreen,
    ArtifactStartScreen,
    artifact_key,
    recognize_artifacts,
    recognize_part,
)
from app.engine.parsing.refusals import Refused, recognize_refusals
from app.engine.settings import ChatsSection
from tests.engine.artifact_texts import (
    BOOK_MAX,
    LEARN_PART,
    SCREEN,
    SCREEN_COLLECTING,
    SCREEN_OLD,
    SCREEN_OLD_HOURS,
    START_LIGHT,
    STARTED_BOOK,
    STARTED_FAX,
    STARTED_LIGHT,
    WALK_PART,
    game_text,
)
from tests.fixtures import game_msg

PARSER = default_parser(ChatsSection())


def test_screen_levels_and_recollect() -> None:
    assert recognize_artifacts(game_text(SCREEN)) == [
        ArtifactsScreen(
            levels={
                "book": 100,
                "fax": 100,
                "light": 84,
                "grade": 77,
                "idea": 69,
                "token": 1,
                "plan": 4,
                "feature": 100,
                "troika": 69,
            },
            recollect=("light",),
        )
    ]


@pytest.mark.parametrize(
    ("text", "artifact", "left_s"),
    [
        (SCREEN_COLLECTING, "light", 3600 + 27 * 60),
        (SCREEN_OLD, "fax", 9 * 86400 + 12 * 3600),
        (SCREEN_OLD_HOURS, "light", 3600),
    ],
)
def test_screen_collect_timer_in_three_formats(text: str, artifact: str, left_s: int) -> None:
    [screen] = recognize_artifacts(game_text(text))
    assert isinstance(screen, ArtifactsScreen)
    # Во время сбора строк «… - /artr_<x>» на экране нет.
    assert (screen.collecting, screen.left_s, screen.recollect) == (artifact, left_s, ())


def test_old_screen_names_with_nbsp() -> None:
    [screen] = recognize_artifacts(game_text(SCREEN_OLD_HOURS))
    assert isinstance(screen, ArtifactsScreen)
    assert screen.levels == {"book": 44, "fax": 74, "light": 81, "grade": 6, "feature": 100}


def test_fixture_screen_is_artifacts_not_info() -> None:
    # Экран Фрости из фикстуры (все пересобираемые — 100): раньше был InfoScreen("artifacts").
    assert PARSER.parse(game_msg("screens", 3568618)) == [
        ArtifactsScreen(
            levels={
                "book": 100,
                "fax": 100,
                "light": 100,
                "grade": 100,
                "idea": 100,
                "token": 100,
                "plan": 100,
                "diploma": 100,
                "keyboard": 100,
                "feature": 100,
                "troika": 94,
            }
        )
    ]


def test_start_screen() -> None:
    assert recognize_artifacts(game_text(START_LIGHT)) == [ArtifactStartScreen(artifact="light")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (STARTED_FAX, ArtifactCollectStarted(artifact="fax", deed="job")),
        (STARTED_BOOK, ArtifactCollectStarted(artifact="book", deed="learn")),
        (STARTED_LIGHT, ArtifactCollectStarted(artifact="light", deed="walk")),
    ],
)
def test_collect_started(text: str, expected: Event) -> None:
    assert recognize_artifacts(game_text(text)) == [expected]


def test_part_line_beside_walk_finish() -> None:
    events = PARSER.parse(game_text(WALK_PART))
    assert [type(e) for e in events] == [ActivityFinished, ArtifactPartFound]
    finished = events[0]
    assert isinstance(finished, ActivityFinished)
    assert (finished.activity, finished.rewards.exp) == ("walk", 74)
    assert events[1] == ArtifactPartFound(artifact="light", level=1)


def test_page_line_in_learn_finish() -> None:
    assert recognize_part(game_text(LEARN_PART)) == [ArtifactPartFound(artifact="book", level=32)]


def test_artifact_max_refusal() -> None:
    assert recognize_refusals(game_text(BOOK_MAX)) == [Refused(reason="artifact_max")]


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("📕\xa0Букваря стартапера", "book"),
        ("📕Букварь Стартапера", "book"),
        ("📠 SW факs", "fax"),
        ("🔦 Фонарь Sw-ет", "light"),
        ("😮Золотой жетон", "token"),
        ("📝Отличный план", "plan"),
        ("💡Идея Стартапера", "idea"),
        ("Неизвестная штука", None),
    ],
)
def test_artifact_key_by_word(name: str, key: str | None) -> None:
    assert artifact_key(name) == key
