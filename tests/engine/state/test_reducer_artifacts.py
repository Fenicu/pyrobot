from datetime import timedelta
from typing import Any

from app.engine.state.model import ArtifactCollect
from app.engine.state.reducer import StateReducer
from tests.engine.artifact_texts import (
    SCREEN,
    SCREEN_COLLECTING,
    STARTED_LIGHT,
    WALK_PART,
    game_text,
)
from tests.engine.state.helpers import PARSER, at, feed, value


def apply(
    reducer: StateReducer, state: dict[str, Any], text: str, minutes: float, msg_id: int = 1
) -> dict[str, Any]:
    msg = game_text(text, at=at(minutes), msg_id=msg_id)
    return reducer.apply(state, msg, PARSER.parse(msg))


def collect(state: dict[str, Any]) -> ArtifactCollect:
    return ArtifactCollect.model_validate(value(state, "artifact_collect"))


def test_screen_levels_without_collect() -> None:
    s = apply(StateReducer(), {}, SCREEN, 0)
    assert value(s, "artifacts")["light"] == 84
    # Строки «Ты уже собираешь» нет — сбора нет, это наблюдение экрана.
    assert value(s, "artifact_collect") is None
    assert s["artifact_collect"]["src"] == "screen"


def test_collect_ends_at_screen_time_plus_left() -> None:
    s = apply(StateReducer(), {}, SCREEN_COLLECTING, 0)
    assert collect(s) == ArtifactCollect(
        artifact="light", ends_at=at(0) + timedelta(hours=1, minutes=27)
    )


def test_start_zeroes_artifact_and_motivation() -> None:
    r = StateReducer()
    s = feed(r, {}, "profile", 3624478, 0)
    assert value(s, "motivation") > 0
    s = apply(r, s, SCREEN, 1, msg_id=2)
    s = apply(r, s, STARTED_LIGHT, 2, msg_id=3)
    assert value(s, "motivation") == 0
    levels = value(s, "artifacts")
    assert (levels["light"], levels["fax"]) == (0, 100)
    assert collect(s) == ArtifactCollect(artifact="light", ends_at=at(2) + timedelta(days=10))


def test_part_sets_level_of_collected_artifact() -> None:
    r = StateReducer()
    s = apply(r, {}, SCREEN, 0)
    s = apply(r, s, STARTED_LIGHT, 1, msg_id=2)
    s = apply(r, s, WALK_PART, 30, msg_id=3)
    assert value(s, "artifacts")["light"] == 1
    assert s["artifacts"]["src"] == "derived"


def test_part_without_known_levels_starts_the_dict() -> None:
    s = apply(StateReducer(), {}, WALK_PART, 0)
    assert value(s, "artifacts") == {"light": 1}
