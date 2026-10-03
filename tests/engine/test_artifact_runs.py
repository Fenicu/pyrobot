from datetime import UTC, datetime, timedelta

import pytest

from app.engine.artifact import ArtifactConflict, ArtifactRuns
from app.engine.notify import Level
from app.engine.settings import ArtifactRunSection, Settings, StaticSettings
from app.engine.state.model import ArtifactCollect, CharacterState, Obs

T0 = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
DAY = timedelta(days=1)
OFF = Settings.model_validate({"features": {"lottery": False}})


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return 0.0


class Notes:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    @property
    def codes(self) -> list[str]:
        return [code for _, code, _ in self.items]

    async def notify(self, level: Level, code: str, text: str) -> None:
        self.items.append((level, code, text))


class Rig:
    def __init__(self, settings: Settings = OFF, state: CharacterState | None = None) -> None:
        self.settings = StaticSettings(settings.model_copy(deep=True))
        self.state = state or CharacterState()
        self.notes = Notes()
        self.clock = Clock(T0)
        self.runs = ArtifactRuns(
            settings=self.settings,
            state=lambda: self.state,
            notifier=self.notes,
            clock=self.clock,
        )

    @property
    def run(self) -> ArtifactRunSection:
        return self.settings.current.artifact_run


def collect(artifact: str, at: datetime, ends: datetime) -> Obs[ArtifactCollect | None]:
    return Obs(value=ArtifactCollect(artifact=artifact, ends_at=ends), at=at)


async def test_scenario_result_activates_run() -> None:
    rig = Rig()
    await rig.runs.start("light", lottery_max=True, by="alice")
    assert rig.run.status == "starting" and rig.settings.current.features.lottery
    await rig.runs.tick()
    assert rig.run.status == "starting" and rig.notes.codes == []
    details = {"artifact": "light", "started_at": T0.isoformat(), "deed": "walk"}
    await rig.runs.started("done", "started", details)
    run = rig.run
    assert (run.status, run.started_at, run.ends_at, run.deed_hint) == (
        "active",
        T0,
        T0 + 10 * DAY,
        "walk",
    )
    assert rig.notes.codes == ["artifact_started"]


async def test_game_hint_outside_tactic_warns() -> None:
    rig = Rig(state=CharacterState(level=Obs(value=10, at=T0)))
    await rig.runs.start("book", lottery_max=False, by="alice")
    details = {"artifact": "book", "started_at": T0.isoformat(), "deed": "learn"}
    await rig.runs.started("done", "started", details)
    assert rig.notes.codes == ["artifact_started", "artifact_tactic_mismatch"]
    assert rig.notes.items[1][0] == "warn"
    # Тактику бот не меняет.
    assert rig.settings.current.artifacts.book_low == ("walk", "job")


async def test_not_recollectable_cancels_start_and_returns_lottery() -> None:
    rig = Rig()
    await rig.runs.start("light", lottery_max=True, by="alice")
    await rig.runs.started("nothing", "not_recollectable", None)
    assert rig.run == ArtifactRunSection()
    assert not rig.settings.current.features.lottery
    assert rig.notes.codes == ["artifact_start_failed"]


async def test_screen_after_request_confirms_unclear_start() -> None:
    rig = Rig()
    await rig.runs.start("light", lottery_max=False, by="alice")
    rig.state = CharacterState(artifact_collect=collect("light", T0, T0 + 9 * DAY))
    await rig.runs.tick()
    assert (rig.run.status, rig.run.ends_at) == ("active", T0 + 9 * DAY)
    assert rig.notes.codes == ["artifact_started"]


async def test_other_artifact_collecting_fails_start() -> None:
    rig = Rig()
    await rig.runs.start("light", lottery_max=True, by="alice")
    rig.state = CharacterState(artifact_collect=collect("fax", T0, T0 + 3 * DAY))
    await rig.runs.tick()
    assert rig.run.status == "idle" and not rig.settings.current.features.lottery
    assert rig.notes.codes == ["artifact_start_failed"]


async def test_tick_finishes_at_ends_at_and_returns_lottery() -> None:
    rig = Rig()
    await rig.runs.start("light", lottery_max=True, by="alice")
    await rig.runs.started("done", "started", {"started_at": T0.isoformat(), "deed": "walk"})
    rig.state = CharacterState(artifacts=Obs(value={"light": 84}, at=T0 + 9 * DAY))
    rig.clock.at = T0 + 10 * DAY
    await rig.runs.tick()
    assert (rig.run.status, rig.run.result_level) == ("finished", 84)
    assert not rig.settings.current.features.lottery
    assert rig.notes.codes == ["artifact_started", "artifact_finished"]
    await rig.runs.tick()
    assert rig.notes.codes == ["artifact_started", "artifact_finished"]


async def test_hundred_finishes_early_and_keeps_lock() -> None:
    rig = Rig()
    await rig.runs.start("light", lottery_max=False, by="alice")
    await rig.runs.started("done", "started", {"started_at": T0.isoformat(), "deed": "walk"})
    rig.state = CharacterState(artifacts=Obs(value={"light": 100}, at=T0 + 8 * DAY))
    rig.clock.at = T0 + 8 * DAY
    await rig.runs.tick()
    assert (rig.run.status, rig.run.result_level, rig.run.ends_at) == (
        "finished",
        100,
        T0 + 10 * DAY,
    )
    assert rig.notes.codes[-1] == "artifact_completed"
    with pytest.raises(ArtifactConflict) as err:
        await rig.runs.start("fax", lottery_max=False, by="alice")
    assert err.value.code == "locked_until"


async def test_level_seen_before_start_does_not_complete() -> None:
    rig = Rig()
    await rig.runs.start("light", lottery_max=False, by="alice")
    await rig.runs.started("done", "started", {"started_at": T0.isoformat(), "deed": "walk"})
    # Уровень 100 снят до старта сбора (прошлый сбор): к нынешнему он не относится.
    rig.state = CharacterState(artifacts=Obs(value={"light": 100}, at=T0 - DAY))
    await rig.runs.tick()
    assert rig.run.status == "active"


async def test_external_collect_notified_once() -> None:
    rig = Rig(state=CharacterState(artifact_collect=collect("fax", T0, T0 + 3 * DAY)))
    await rig.runs.tick()
    await rig.runs.tick()
    assert rig.notes.codes == ["artifact_collect_external"]
    await rig.runs.adopt(by="alice")
    assert (rig.run.status, rig.run.artifact, rig.run.ends_at) == ("active", "fax", T0 + 3 * DAY)
    await rig.runs.tick()
    assert rig.notes.codes == ["artifact_collect_external"]


async def test_cancel_wins_over_late_scenario_result() -> None:
    rig = Rig()
    await rig.runs.start("light", lottery_max=False, by="alice")
    await rig.runs.cancel(by="alice")
    await rig.runs.started("done", "started", {"started_at": T0.isoformat(), "deed": "walk"})
    assert rig.run.status == "idle" and rig.notes.codes == []


async def test_pause_resume_and_conflicts() -> None:
    rig = Rig()
    with pytest.raises(ArtifactConflict) as err:
        await rig.runs.pause(by="alice")
    assert err.value.code == "no_run"
    await rig.runs.start("light", lottery_max=False, by="alice")
    await rig.runs.started("done", "started", {"started_at": T0.isoformat(), "deed": "walk"})
    await rig.runs.pause(by="alice")
    assert rig.run.status == "paused"
    await rig.runs.resume(by="alice")
    rig.state = CharacterState(artifacts=Obs(value={"light": 37}, at=T0 + DAY))
    await rig.runs.cancel(by="alice")
    assert (rig.run.status, rig.run.result_level) == ("cancelled", 37)
    assert rig.settings.version == 5


async def test_late_result_of_other_artifact_does_not_activate_new_request() -> None:
    rig = Rig()
    await rig.runs.start("light", lottery_max=False, by="alice")
    await rig.runs.cancel(by="alice")
    await rig.runs.start("fax", lottery_max=False, by="alice")
    details = {"artifact": "light", "started_at": T0.isoformat(), "deed": "walk"}
    await rig.runs.started("done", "started", details)
    assert (rig.run.status, rig.run.artifact) == ("starting", "fax")
    assert rig.notes.codes == []
    await rig.runs.started("nothing", "not_recollectable", {"artifact": "light"})
    assert (rig.run.status, rig.run.artifact) == ("starting", "fax")
    assert rig.notes.codes == []
