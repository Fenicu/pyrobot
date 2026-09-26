from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from app.engine.gateway.types import (
    ActionKind,
    ActionRequest,
    ActionStatus,
    Match,
    Source,
    Verdict,
)
from app.engine.settings import Settings
from app.engine.transport.fake import Sent
from app.engine.types import Button
from tests.engine.gateway_rig import LIVE, Rig, expect_text, running_rig, send
from tests.engine.helpers import GAME, make_msg, now


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    async for r in running_rig():
        yield r


async def test_forbidden_never_sent(rig: Rig) -> None:
    res = await rig.gw.submit(send("/changecompany", expect=expect_text("x")))
    assert res.status is ActionStatus.REJECTED and res.reason == "forbidden"
    assert rig.transport.sent == []
    assert rig.store.rows[res.action_id or 0].status is ActionStatus.REJECTED


async def test_donate_never_sent(rig: Rig) -> None:
    res = await rig.gw.submit(
        send("/finish", source=Source.MANUAL, risky_confirmed=True, expect=expect_text("x"))
    )
    assert res.status is ActionStatus.REJECTED and res.reason == "donate"
    assert rig.transport.sent == []


async def test_action_without_expectation_rejected(rig: Rig) -> None:
    res = await rig.gw.submit(send("/job"))
    assert res.status is ActionStatus.REJECTED and res.reason == "expectation_required"
    assert rig.transport.sent == []


async def test_risky_needs_manual_confirm(rig: Rig) -> None:
    exp = expect_text("перерабатываешь")
    res = await rig.gw.submit(send("⚪️ → 🔵", expect=exp))
    assert res.status is ActionStatus.REJECTED and res.reason == "risky_requires_confirm"
    rig.reply_with("Ты перерабатываешь улучшения")
    res = await rig.gw.submit(
        send("⚪️ → 🔵", source=Source.MANUAL, risky_confirmed=True, expect=exp)
    )
    assert res.status is ActionStatus.CONFIRMED


async def test_dry_run_suppresses_actions_but_sends_nav() -> None:
    dry = Settings(engine=LIVE.engine.model_copy(update={"mode": "dry_run"}))
    async for r in running_rig(dry):
        res = await r.gw.submit(send("/harvest", expect=expect_text("Барахолку")))
        assert res.status is ActionStatus.SUPPRESSED and res.reason == "dry_run"
        assert (await r.gw.submit(send("😎Я"))).status is ActionStatus.CONFIRMED
        assert [s.payload for s in r.transport.sent] == ["😎Я"]


async def test_kill_switch_from_settings_and_latch(rig: Rig) -> None:
    await rig.settings.update(
        lambda s: s.model_copy(update={"engine": s.engine.model_copy(update={"killed": True})}),
        changed_by="t",
    )
    assert (await rig.gw.submit(send("😎Я"))).reason == "kill_switch"
    await rig.settings.update(
        lambda s: s.model_copy(update={"engine": s.engine.model_copy(update={"killed": False})}),
        changed_by="t",
    )
    await rig.gw.kill("manual")
    assert rig.gw.kill_reason == "manual"
    assert (await rig.gw.submit(send("😎Я"))).status is ActionStatus.SUPPRESSED
    await rig.gw.unkill()
    assert (await rig.gw.submit(send("😎Я"))).status is ActionStatus.CONFIRMED
    assert [s.payload for s in rig.transport.sent] == ["😎Я"]


async def test_intent_persisted_before_send(rig: Rig) -> None:
    seen: list[ActionStatus] = []
    rig.transport.before_send = lambda rec: seen.append(rig.store.rows[1].status)
    rig.reply_with("Ты отправился работать")
    res = await rig.gw.submit(send("/job", expect=expect_text("работать")))
    assert seen == [ActionStatus.INTENT]
    assert res.status is ActionStatus.CONFIRMED
    assert res.match == Match(Verdict.CONFIRMED, "работать")
    assert rig.store.rows[1].history == [
        ActionStatus.INTENT,
        ActionStatus.SENT,
        ActionStatus.CONFIRMED,
    ]


async def test_refused_by_expectation(rig: Rig) -> None:
    rig.reply_with("❗️Ты занят другим делом ещё 3 мин.")
    res = await rig.gw.submit(send("/job", expect=expect_text("работать", refuse="занят")))
    assert res.status is ActionStatus.REFUSED and res.reason == "занят"


async def test_timeout_is_outcome_unknown_without_retry(rig: Rig) -> None:
    res = await rig.gw.submit(send("/job", expect=expect_text("работать", timeout=0.1)))
    assert res.status is ActionStatus.OUTCOME_UNKNOWN and res.reason == "timeout"
    assert len(rig.transport.sent) == 1


async def test_nav_without_expectation_confirms_after_send(rig: Rig) -> None:
    res = await rig.gw.submit(send("😎Я"))
    assert res.status is ActionStatus.CONFIRMED and res.reason == "sent"


async def test_stale_deliveries_ignored(rig: Rig) -> None:
    async def responder(rec: Sent) -> None:
        await rig.deliver(make_msg("Ты отправился работать"), journal_id=0)
        await rig.deliver(make_msg("Ты отправился работать", date=now() - timedelta(minutes=5)))
        await rig.deliver(make_msg("Ты отправился работать", outgoing=True))
        await rig.deliver(make_msg("Ты отправился работать", chat_id=42))

    rig.transport.responder = responder
    res = await rig.gw.submit(send("/job", expect=expect_text("работать", timeout=0.1)))
    assert res.status is ActionStatus.OUTCOME_UNKNOWN


async def test_click_checks_latest_revision(rig: Rig) -> None:
    exp = expect_text("Вверх")
    req = ActionRequest(
        kind=ActionKind.CLICK, chat_id=GAME, message_id=77, data="maze_up", expect=exp
    )
    assert (await rig.gw.submit(req)).reason == "stale_button"
    btn = (Button("⬆️", 0, 1, data="maze_up"),)
    rig.latest[(GAME, 77)] = make_msg("карта", msg_id=77, kind="edit", revision=10, buttons=btn)
    stale = ActionRequest(
        kind=ActionKind.CLICK,
        chat_id=GAME,
        message_id=77,
        data="maze_up",
        expect=exp,
        expect_revision=9,
    )
    assert (await rig.gw.submit(stale)).reason == "stale_revision"
    rig.transport.toast = "⬆️Идёшь Вверх"

    async def responder(rec: Sent) -> None:
        await rig.deliver(make_msg("🔋88%\nВверх", msg_id=77, kind="edit", revision=12))

    rig.transport.responder = responder
    res = await rig.gw.submit(
        ActionRequest(
            kind=ActionKind.CLICK,
            chat_id=GAME,
            message_id=77,
            data="maze_up",
            expect=exp,
            expect_revision=10,
        )
    )
    assert res.status is ActionStatus.CONFIRMED and res.answer == "⬆️Идёшь Вверх"


async def test_expired_request_rejected(rig: Rig) -> None:
    res = await rig.gw.submit(send("😎Я", ttl_s=0.0))
    assert res.status is ActionStatus.REJECTED and res.reason == "expired"
