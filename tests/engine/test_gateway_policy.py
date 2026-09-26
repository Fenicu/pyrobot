from collections.abc import AsyncIterator

import pytest

from app.engine.gateway.types import ActionStatus, Source
from app.engine.settings import Settings
from tests.engine.gateway_rig import LIVE, Rig, expect_text, running_rig, send


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    async for r in running_rig():
        yield r


async def _pause(rig: Rig, **flags: bool) -> None:
    await rig.settings.update(
        lambda s: s.model_copy(
            update={"engine": s.engine.model_copy(update={"paused": True, **flags})}
        ),
        changed_by="test",
    )


@pytest.mark.parametrize(
    ("source", "flags", "status"),
    [
        (Source.PLANNER, {}, ActionStatus.REJECTED),
        (Source.SCENARIO, {}, ActionStatus.REJECTED),
        (Source.URGENT, {}, ActionStatus.CONFIRMED),
        (Source.URGENT, {"urgent_while_paused": False}, ActionStatus.REJECTED),
        (Source.MANUAL, {}, ActionStatus.CONFIRMED),
        (Source.MANUAL, {"manual_while_paused": False}, ActionStatus.REJECTED),
    ],
)
async def test_pause_by_source(
    rig: Rig, source: Source, flags: dict[str, bool], status: ActionStatus
) -> None:
    await _pause(rig, **flags)
    rig.reply_with("Ты отправился работать")
    res = await rig.gw.submit(send("/job", source=source, expect=expect_text("работать")))
    assert res.status is status
    if status is ActionStatus.REJECTED:
        assert res.reason == "paused" and rig.transport.sent == []


async def test_pause_keeps_nav(rig: Rig) -> None:
    await _pause(rig)
    assert (await rig.gw.submit(send("😎Я", source=Source.PLANNER))).status is (
        ActionStatus.CONFIRMED
    )


async def test_feature_off_rejects_all_but_manual(rig: Rig) -> None:
    await rig.settings.update(
        lambda s: s.model_copy(
            update={"features": s.features.model_copy(update={"books": False})}
        ),
        changed_by="test",
    )
    res = await rig.gw.submit(send("/read_exp", expect=expect_text("книг")))
    assert res.status is ActionStatus.REJECTED and res.reason == "feature_off:books"
    rig.reply_with("Прочитал книгу")
    manual = await rig.gw.submit(
        send("/read_exp", source=Source.MANUAL, expect=expect_text("книг"))
    )
    assert manual.status is ActionStatus.CONFIRMED
    assert [s.payload for s in rig.transport.sent] == ["/read_exp"]


async def test_disabled_by_default_features_rejected(rig: Rig) -> None:
    res = await rig.gw.submit(send("/tickets_all", expect=expect_text("x")))
    assert res.status is ActionStatus.REJECTED and res.reason == "feature_off:lottery"


async def test_simulated_step_suppressed_in_live_nav_sent(rig: Rig) -> None:
    res = await rig.gw.submit(send("/job", simulate=True, expect=expect_text("работать")))
    assert res.status is ActionStatus.SUPPRESSED and res.reason == "uncertified"
    assert (await rig.gw.submit(send("😎Я", simulate=True))).status is ActionStatus.CONFIRMED
    assert [s.payload for s in rig.transport.sent] == ["😎Я"]


async def test_dry_run_reason_wins_over_simulate() -> None:
    dry = Settings(engine=LIVE.engine.model_copy(update={"mode": "dry_run"}))
    async for r in running_rig(dry):
        res = await r.gw.submit(send("/job", simulate=True, expect=expect_text("x")))
        assert res.status is ActionStatus.SUPPRESSED and res.reason == "dry_run"
