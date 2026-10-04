import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest

from app.engine.gateway.gateway import RECONCILE_REASON
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus, Expectation, Source
from app.engine.settings import ArtifactRunSection, Settings
from app.engine.transport.fake import Sent
from app.engine.types import Button, IncomingMessage
from tests.engine.gateway_rig import LIVE, Rig, expect_text, running_rig, send
from tests.engine.helpers import GAME, make_msg


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
    res = await rig.gw.submit(send("/capitalization", expect=expect_text("x")))
    assert res.status is ActionStatus.REJECTED and res.reason == "feature_off:paid_info"


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


async def test_answer_expected_in_other_chat(rig: Rig) -> None:
    tangerine = -1001377961602

    async def refuse(rec: Sent) -> None:
        await rig.deliver(make_msg("❌Увы, Настя пока не играет в StartupWars.", msg_id=901))

    rig.transport.responder = refuse
    req = ActionRequest(
        kind=ActionKind.SEND,
        chat_id=tangerine,
        text="/gt",
        reply_to=927136,
        expect=Expectation(expect_text("не играет").predicate, 0.3, chat_id=GAME),
    )
    res = await rig.gw.submit(req)
    assert res.status is ActionStatus.CONFIRMED
    [sent] = rig.transport.sent
    assert (sent.chat_id, sent.payload) == (tangerine, "/gt")


async def test_run_pinned_to_dry_run_suppressed_in_live(rig: Rig) -> None:
    res = await rig.gw.submit(send("/job", dry_run=True, expect=expect_text("работать")))
    assert res.status is ActionStatus.SUPPRESSED and res.reason == "dry_run"
    assert (await rig.gw.submit(send("😎Я", dry_run=True))).status is ActionStatus.CONFIRMED
    assert [s.payload for s in rig.transport.sent] == ["😎Я"]


async def _artifact_run(rig: Rig, artifact: str | None, status: str) -> None:
    run = ArtifactRunSection.model_validate({"artifact": artifact, "status": status})
    await rig.settings.update(
        lambda s: s.model_copy(update={"artifact_run": run}), changed_by="test"
    )


def _accept(rig: Rig, scenario: str | None = "artifact_start") -> ActionRequest:
    data = "artr_light_accept"
    button = (Button("👍Стартуем!", 0, 0, data=data),)
    rig.latest[(GAME, 77)] = make_msg("Старт сбора артефакта", msg_id=77, buttons=button)
    return ActionRequest(
        kind=ActionKind.CLICK,
        chat_id=GAME,
        message_id=77,
        data=data,
        source=Source.SCENARIO,
        scenario=scenario,
        expect=expect_text("Сбор начат!"),
    )


@pytest.mark.parametrize(
    ("artifact", "status", "scenario", "sent"),
    [
        ("light", "starting", "artifact_start", True),
        ("fax", "starting", "artifact_start", False),
        ("light", "active", "artifact_start", False),
        ("light", "starting", None, False),
        ("light", "starting", "deed:walk", False),
    ],
)
async def test_artifact_accept_only_from_start_scenario(
    rig: Rig, artifact: str, status: str, scenario: str | None, sent: bool
) -> None:
    await _artifact_run(rig, artifact, status)
    rig.reply_with("Сбор начат!")
    res = await rig.gw.submit(_accept(rig, scenario))
    if sent:
        assert res.status is ActionStatus.CONFIRMED
        assert [s.payload for s in rig.transport.sent] == ["artr_light_accept"]
    else:
        assert (res.status, res.reason) == (ActionStatus.REJECTED, "risky_requires_confirm")
        assert rig.transport.sent == []


async def test_artifact_accept_rechecked_before_send(rig: Rig) -> None:
    # Пока клик ждал очереди, запуск отменили: перед отправкой — снова по текущей записи.
    await _artifact_run(rig, "light", "starting")
    lease = await rig.gw.acquire_lease("other")
    pending = asyncio.create_task(rig.gw.submit(_accept(rig)))
    await asyncio.sleep(0.02)
    await _artifact_run(rig, None, "idle")
    await rig.gw.release_lease(lease)
    res = await pending
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "risky_requires_confirm")
    assert rig.transport.sent == []


async def test_manual_accept_still_needs_confirmation(rig: Rig) -> None:
    await _artifact_run(rig, "light", "starting")
    # Ручной клик без токена подтверждения — как любой risky.
    res = await rig.gw.submit(replace(_accept(rig, None), source=Source.MANUAL))
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "risky_requires_confirm")


async def test_reconcile_required_allows_metro_moves_but_blocks_purchases(rig: Rig) -> None:
    rig.gw.block_spending(RECONCILE_REASON)
    btn_left = (Button("⬅️", 0, 1, data="maze_left"),)
    btn_tokens = (Button("⚡️Баф", 0, 0, data="maze_buf_tokens_fastMove"),)
    btn_coins = (Button("⚡️Баф 🌐", 0, 0, data="maze_buf_coins_fastMove"),)
    rig.latest[(GAME, 77)] = make_msg(
        "карта", msg_id=77, buttons=(*btn_left, *btn_tokens, *btn_coins)
    )

    rig.reply_with("Идёшь Влево.")
    move = await rig.gw.submit(
        ActionRequest(
            kind=ActionKind.CLICK,
            chat_id=GAME,
            message_id=77,
            data="maze_left",
            expect=expect_text("Идёшь"),
        )
    )
    assert move.status is ActionStatus.CONFIRMED
    assert [s.payload for s in rig.transport.sent] == ["maze_left"]

    tokens = await rig.gw.submit(
        ActionRequest(
            kind=ActionKind.CLICK,
            chat_id=GAME,
            message_id=77,
            data="maze_buf_tokens_fastMove",
            expect=expect_text("Баф"),
        )
    )
    assert (tokens.status, tokens.reason) == (
        ActionStatus.REJECTED,
        f"blocked:{RECONCILE_REASON}",
    )

    coins = await rig.gw.submit(
        ActionRequest(
            kind=ActionKind.CLICK,
            chat_id=GAME,
            message_id=77,
            data="maze_buf_coins_fastMove",
            expect=expect_text("Баф"),
        )
    )
    assert (coins.status, coins.reason) == (ActionStatus.REJECTED, "donate")

    enter = await rig.gw.submit(
        ActionRequest(
            kind=ActionKind.CLICK,
            chat_id=GAME,
            message_id=77,
            data="maze_enter_accept",
            expect=expect_text("Вход"),
        )
    )
    assert (enter.status, enter.reason) == (
        ActionStatus.REJECTED,
        f"blocked:{RECONCILE_REASON}",
    )


async def test_metro_move_uncertain_does_not_block_spending(rig: Rig) -> None:
    btn_left = (Button("⬅️", 0, 1, data="maze_left"),)
    rig.latest[(GAME, 77)] = make_msg("карта", msg_id=77, buttons=btn_left)
    res = await rig.gw.submit(
        ActionRequest(
            kind=ActionKind.CLICK,
            chat_id=GAME,
            message_id=77,
            data="maze_left",
            expect=Expectation(lambda d: None, 0.05),
        )
    )
    assert res.status is ActionStatus.OUTCOME_UNKNOWN
    assert rig.gw.spending_blocked is None


@pytest.mark.parametrize(
    ("data", "spend_free"),
    [
        ("maze_up", True),
        ("maze_down", True),
        ("maze_left", True),
        ("maze_right", True),
        ("maze_start", True),
        ("maze_exit", True),
        ("maze_exit_accept", True),
        ("maze_exit_decline", True),
        ("maze_npc_low_accept", True),
        ("maze_npc_low_decline", True),
        ("maze_npc_high_accept", True),
        ("maze_npc_high_decline", True),
        ("maze_chest_accept", True),
        ("maze_chest_decline", True),
        ("maze_first_aid", True),
        ("maze_first_aid_accept", True),
        ("maze_first_aid_decline", True),
        ("maze_continue", True),
        ("maze_cancel_move", True),
        ("maze_enter_decline", True),
        ("maze_buf_tokens_fastMove", False),
        ("maze_buf_coins_fastMove", False),
        ("maze_enter_accept", False),
        # Незнакомые кнопки диалогов метро тратами не считаются заранее.
        ("maze_exit_buy_tokens", False),
        ("maze_npc_any", False),
    ],
)
def test_metro_spends_nothing_classification(data: str, spend_free: bool) -> None:
    from app.engine.commands import spends_nothing_callback

    assert spends_nothing_callback(data) is spend_free


async def test_spending_block_keeps_pause_and_dry_run_reasons(rig: Rig) -> None:
    rig.gw.block_spending(RECONCILE_REASON)
    await _pause(rig)
    paused = await rig.gw.submit(send("/job", source=Source.SCENARIO, expect=expect_text("x")))
    assert (paused.status, paused.reason) == (ActionStatus.REJECTED, "paused")
    await rig.settings.update(
        lambda s: s.model_copy(
            update={"engine": s.engine.model_copy(update={"paused": False, "mode": "dry_run"})}
        ),
        changed_by="test",
    )
    dry = await rig.gw.submit(send("/job", source=Source.MANUAL, expect=expect_text("x")))
    assert (dry.status, dry.reason) == (ActionStatus.SUPPRESSED, "dry_run")
    assert rig.transport.sent == []


async def test_unknown_message_reread_only_for_click_that_may_be_sent(rig: Rig) -> None:
    reads: list[tuple[int, int]] = []

    async def reread(chat_id: int, msg_id: int) -> IncomingMessage | None:
        reads.append((chat_id, msg_id))
        return None

    rig.gw._reread = reread

    def click(chat_id: int) -> ActionRequest:
        return ActionRequest(
            kind=ActionKind.CLICK,
            chat_id=chat_id,
            message_id=5,
            data="maze_left",
            expect=expect_text("x"),
        )

    foreign = await rig.gw.submit(click(42))
    assert (foreign.status, foreign.reason) == (ActionStatus.REJECTED, "chat_not_allowed")
    rig.block = "tg_offline"
    offline = await rig.gw.submit(click(GAME))
    assert (offline.status, offline.reason) == (ActionStatus.REJECTED, "tg_offline")
    assert reads == []
    rig.block = None
    unknown = await rig.gw.submit(click(GAME))
    assert (unknown.status, unknown.reason) == (ActionStatus.REJECTED, "stale_button")
    assert reads == [(GAME, 5)]
