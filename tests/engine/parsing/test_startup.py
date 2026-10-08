from app.engine.events import Event
from app.engine.parsing.activities import (
    _STARTS,
    ActivityCancelled,
    ActivityFinished,
    ActivityStarted,
    Price,
    PricesScreen,
    StartupLevelUp,
    StartupScreen,
)
from app.engine.parsing.activities import (
    RECOGNIZERS as ACTIVITIES_RECOGNIZERS,
)
from app.engine.parsing.common import Rewards
from app.engine.parsing.refusals import RECOGNIZERS as REFUSALS_RECOGNIZERS
from app.engine.parsing.refusals import Refused
from tests.engine.startup_texts import (
    DECLINE_NOTHING,
    DECLINED,
    MAX_REFUSAL,
    NO_MOTIVATION,
    REFUSAL_LOW_LEVEL,
    RESULT_KEYS,
    RESULT_LEVELUP,
    RESULT_NO_REWARD,
    RESULT_PARTS,
    RESULT_PLAIN,
    SCREEN_FROSTY,
    SCREEN_IN_PROGRESS,
    SCREEN_MAX,
    START,
    startup_msg,
)
from tests.fixtures import game_msg


def _events(text: str) -> list[Event]:
    msg = startup_msg(text)
    return [
        e for recognize in (*ACTIVITIES_RECOGNIZERS, *REFUSALS_RECOGNIZERS) for e in recognize(msg)
    ]


def test_start_is_a_startup_deed_of_8_minutes() -> None:
    assert _events(START) == [ActivityStarted(activity="startup", duration_s=480)]


def test_start_pattern_matches_only_startup() -> None:
    assert [kind for kind, pattern in _STARTS if pattern.match(START)] == ["startup"]


def test_result_with_parts() -> None:
    [event] = _events(RESULT_PARTS)
    assert isinstance(event, ActivityFinished)
    assert (event.activity, event.failed) == ("startup", False)
    assert event.rewards == Rewards(exp=55, knowledge=-6, details=-9, startup_progress=6)


def test_result_plain_without_details() -> None:
    [event] = _events(RESULT_PLAIN)
    assert isinstance(event, ActivityFinished)
    assert event.rewards == Rewards(exp=78, knowledge=-6, startup_progress=6)


def test_result_with_keys() -> None:
    [event] = _events(RESULT_KEYS)
    assert isinstance(event, ActivityFinished)
    assert event.rewards == Rewards(exp=52, knowledge=-5, details=-8, startup_progress=5, keys=50)


def test_result_without_reward() -> None:
    [event] = _events(RESULT_NO_REWARD)
    assert isinstance(event, ActivityFinished)
    assert (event.activity, event.failed) == ("startup", False)
    assert event.rewards == Rewards()


def test_levelup_tail_adds_event() -> None:
    events = _events(RESULT_LEVELUP)
    assert len(events) == 2
    assert isinstance(events[0], ActivityFinished) and isinstance(events[1], StartupLevelUp)
    assert events[0].rewards == Rewards(exp=84, knowledge=-7, startup_progress=7)


def test_decline_is_cancellation_not_result() -> None:
    assert _events(DECLINED) == [ActivityCancelled(result="ok")]


def test_startup_refusals() -> None:
    assert _events(MAX_REFUSAL) == [Refused(reason="startup_max")]
    assert _events(REFUSAL_LOW_LEVEL) == [Refused(reason="startup_level", need=18)]


def test_other_startup_answers_keep_their_events() -> None:
    assert _events(NO_MOTIVATION) == [Refused(reason="no_motivation")]
    assert _events(DECLINE_NOTHING) == [ActivityCancelled(result="nothing")]


def test_screen_in_progress() -> None:
    events = _events(SCREEN_IN_PROGRESS)
    assert events == [
        PricesScreen(
            screen="startup",
            prices={
                "learn": Price(motivation=2, minutes=7),
                "confa": Price(motivation=3, money=7, minutes=8),
            },
        ),
        StartupScreen(
            level=5,
            max=False,
            progress=149,
            progress_needed=None,
            price={"motivation": 2, "raw": 2, "minutes": 8},
        ),
    ]


def test_screen_with_known_threshold() -> None:
    events = _events(SCREEN_MAX)
    assert events == [
        PricesScreen(
            screen="startup",
            prices={
                "learn": Price(motivation=2, minutes=7),
                "confa": Price(motivation=3, money=7, minutes=8),
            },
        ),
        StartupScreen(
            level=6,
            max=False,
            progress=0,
            progress_needed=1200,
            price={"motivation": 2, "raw": 2, "minutes": 8},
        ),
    ]


def test_frosty_level0_screen_without_confa() -> None:
    assert _events(SCREEN_FROSTY) == [
        StartupScreen(
            level=0,
            max=False,
            progress=0,
            progress_needed=100,
            price={"motivation": 2, "minutes": 8},
        )
    ]


def test_max_screen_price_unknown() -> None:
    msg = game_msg("activities", 3624645)
    events = [e for recognize in ACTIVITIES_RECOGNIZERS for e in recognize(msg)]
    assert events == [
        PricesScreen(
            screen="startup",
            prices={
                "learn": Price(motivation=2, minutes=7),
                "confa": Price(motivation=3, money=7, minutes=8),
            },
        ),
        StartupScreen(level=8, max=True, progress=None, progress_needed=None, price=None),
    ]
