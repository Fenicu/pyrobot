from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.engine.artifact import (
    ArtifactConflict,
    Pace,
    activate,
    activate_seen,
    adopt,
    artifact_deeds,
    artifact_view,
    cancel,
    fail_start,
    finish,
    pause,
    resume,
    start,
    tactic_mismatch,
)
from app.engine.settings import (
    ArtifactKey,
    ArtifactLottery,
    ArtifactRunSection,
    ArtifactsSection,
    LotterySection,
    Settings,
    apply_patch,
)
from app.engine.state.model import ArtifactCollect, CharacterState, Obs

T0 = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
DAY = timedelta(days=1)
OFF = Settings.model_validate(
    {
        "features": {"lottery": False},
        "lottery": {"tickets": {"money": 3, "knowledge": 0}, "keep": {"money": 500}},
    }
)


def levels(**known: int) -> CharacterState:
    return CharacterState(artifacts=Obs(value=known, at=T0))


def with_run(**run: object) -> Settings:
    return Settings(artifact_run=ArtifactRunSection.model_validate(run))


def going(
    artifact: ArtifactKey = "light", settings: Settings = OFF, *, lottery_max: bool = False
) -> Settings:
    started = start(settings, CharacterState(), artifact, lottery_max=lottery_max, now=T0)
    return activate(started, T0, "walk")


def test_deeds_by_artifact_and_character_level() -> None:
    tactic = ArtifactsSection()
    assert artifact_deeds(tactic, "light", 50) == ("walk",)
    assert artifact_deeds(tactic, "fax", 50) == ("job",)
    assert artifact_deeds(tactic, "book", 17) == ("walk", "job")
    assert artifact_deeds(tactic, "book", 18) == ("learn",)
    # Уровень персонажа неизвестен — как с 18-го.
    assert artifact_deeds(tactic, "book", None) == ("learn",)
    assert artifact_deeds(tactic, None, 50) == ()


@pytest.mark.parametrize(
    "data",
    [{"light": []}, {"book_low": ["walk", "walk"]}, {"fax": ["walk"]}, {"book_high": ["job"]}],
)
def test_tactic_lists_are_validated(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ArtifactsSection.model_validate(data)


def test_start_switches_lottery_to_max_and_cancel_returns_it() -> None:
    s = start(OFF, levels(light=84), "light", lottery_max=True, now=T0)
    run = s.artifact_run
    assert (run.status, run.artifact, run.requested_at) == ("starting", "light", T0)
    assert s.features.lottery and s.lottery == LotterySection()
    assert run.lottery_before == ArtifactLottery(enabled=False, lottery=OFF.lottery)
    assert run.lottery_applied == ArtifactLottery(enabled=True, lottery=LotterySection())
    back = cancel(s, None)
    assert back.artifact_run == ArtifactRunSection()
    assert (back.features.lottery, back.lottery) == (False, OFF.lottery)


def test_lottery_changed_by_user_is_kept() -> None:
    s = going(lottery_max=True)
    s = apply_patch(s, {"lottery": {"keep": {"money": 50}}})
    done = finish(s, 40)
    assert done.features.lottery and done.lottery.keep.money == 50
    assert done.artifact_run.lottery_applied is None
    assert (done.artifact_run.status, done.artifact_run.result_level) == ("finished", 40)


def test_lottery_already_on_is_not_touched() -> None:
    s = start(Settings(), CharacterState(), "fax", lottery_max=True, now=T0)
    assert s.artifact_run.lottery_applied is None and s.lottery == Settings().lottery


@pytest.mark.parametrize(
    ("settings", "state", "code"),
    [
        (
            with_run(artifact="fax", status="active", ends_at=T0 + DAY),
            CharacterState(),
            "run_in_progress",
        ),
        (with_run(artifact="fax", status="starting"), CharacterState(), "run_in_progress"),
        (
            with_run(artifact="fax", status="finished", ends_at=T0 + DAY),
            CharacterState(),
            "locked_until",
        ),
        (
            Settings(),
            CharacterState(
                artifact_collect=Obs(
                    value=ArtifactCollect(artifact="fax", ends_at=T0 + DAY), at=T0
                )
            ),
            "locked_until",
        ),
        (Settings(), levels(light=100), "artifact_max"),
    ],
)
def test_start_blockers(settings: Settings, state: CharacterState, code: str) -> None:
    with pytest.raises(ArtifactConflict) as err:
        start(settings, state, "light", lottery_max=False, now=T0)
    assert err.value.code == code


def test_lock_after_ends_at_is_over() -> None:
    old = with_run(artifact="fax", status="finished", ends_at=T0 - DAY)
    assert (
        start(old, CharacterState(), "light", lottery_max=False, now=T0).artifact_run.status
        == "starting"
    )


def test_pause_resume_cancel() -> None:
    s = going()
    assert s.artifact_run.ends_at == T0 + timedelta(days=10)
    paused = pause(s)
    assert paused.artifact_run.status == "paused"
    with pytest.raises(ArtifactConflict) as err:
        pause(paused)
    assert err.value.code == "no_run"
    resumed = resume(paused)
    assert resumed.artifact_run.status == "active"
    done = cancel(resumed, 37)
    run = done.artifact_run
    assert (run.status, run.result_level, run.ends_at) == (
        "cancelled",
        37,
        T0 + timedelta(days=10),
    )
    with pytest.raises(ArtifactConflict):
        cancel(done, 37)


def test_activate_by_screen_and_failed_start() -> None:
    s = start(OFF, CharacterState(), "light", lottery_max=True, now=T0)
    seen = ArtifactCollect(artifact="light", ends_at=T0 + timedelta(days=9))
    run = activate_seen(s, seen).artifact_run
    assert (run.status, run.started_at, run.ends_at) == ("active", T0 - DAY, seen.ends_at)
    with pytest.raises(ArtifactConflict) as err:
        activate_seen(s, ArtifactCollect(artifact="fax", ends_at=seen.ends_at))
    assert err.value.code == "other_artifact"
    failed = fail_start(s)
    assert failed.artifact_run == ArtifactRunSection() and not failed.features.lottery


def test_activate_only_while_starting() -> None:
    with pytest.raises(ArtifactConflict):
        activate(Settings(), T0, "walk")


def test_adopt_only_game_collect_without_record() -> None:
    collect = ArtifactCollect(artifact="fax", ends_at=T0 + 3 * DAY)
    s = adopt(Settings(), collect, T0)
    run = s.artifact_run
    assert (run.status, run.artifact, run.ends_at, run.lottery_before) == (
        "active",
        "fax",
        collect.ends_at,
        None,
    )
    with pytest.raises(ArtifactConflict) as own:
        adopt(cancel(s, 5), collect, T0)
    assert own.value.code == "not_external"
    with pytest.raises(ArtifactConflict) as none:
        adopt(Settings(), None, T0)
    assert none.value.code == "not_collecting"
    with pytest.raises(ArtifactConflict) as busy:
        adopt(s, collect, T0)
    assert busy.value.code == "run_in_progress"


def test_tactic_mismatch_by_game_hint() -> None:
    s = activate(
        start(Settings(), CharacterState(), "book", lottery_max=False, now=T0), T0, "learn"
    )
    assert tactic_mismatch(s, 10)
    assert not tactic_mismatch(s, 30)


def test_view_progress_pace_and_lock() -> None:
    s = going(settings=Settings())
    later = T0 + 2 * DAY
    state = CharacterState(
        level=Obs(value=54, at=later),
        artifacts=Obs(value={"light": 10, "fax": 100}, at=later),
        artifact_collect=Obs(
            value=ArtifactCollect(artifact="light", ends_at=T0 + timedelta(days=10)), at=later
        ),
    )
    view = artifact_view(s, state, later)
    assert view.level == 10
    assert view.pace == Pace(levels_per_day=5.0, forecast_level=50)
    assert view.next_start_at == T0 + timedelta(days=10)
    assert view.external is False
    assert view.tactic == {"book": ("learn",), "fax": ("job",), "light": ("walk",)}
    assert (view.lottery_on, view.lottery_on_start) == (True, True)
    over = artifact_view(cancel(s, 12), state, later)
    assert (over.level, over.pace) == (12, Pace(None, None))


def test_start_refused_when_deeds_disabled() -> None:
    off = Settings.model_validate({"features": {"deeds": False}})
    with pytest.raises(ArtifactConflict) as err:
        start(off, CharacterState(), "light", lottery_max=True, now=T0)
    assert err.value.code == "deeds_disabled"
    assert off.artifact_run == ArtifactRunSection() and off.features.lottery

    running = off.model_copy(
        update={
            "artifact_run": ArtifactRunSection(artifact="fax", status="active", ends_at=T0 + DAY)
        }
    )
    with pytest.raises(ArtifactConflict) as busy:
        start(running, CharacterState(), "light", lottery_max=False, now=T0)
    assert busy.value.code == "run_in_progress"
