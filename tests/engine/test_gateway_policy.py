import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.engine.gateway.gateway import RECONCILE_REASON, command_feature
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus, Expectation, Source
from app.engine.settings import ArtifactRunSection, GadgetUpgradeSection, Settings
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


async def test_evicted_message_recalled_from_journal_before_reread(rig: Rig) -> None:
    reads: list[tuple[int, int]] = []
    recalls: list[tuple[int, int]] = []
    btn = (Button("⬅️", 0, 0, data="maze_left"),)
    journaled = {5: make_msg("карта", msg_id=5, revision=3, buttons=btn)}

    async def reread(chat_id: int, msg_id: int) -> IncomingMessage | None:
        reads.append((chat_id, msg_id))
        return None

    async def recall(chat_id: int, msg_id: int) -> IncomingMessage | None:
        # Как Pipeline.recall: найденное в журнале становится последней ревизией кэша.
        recalls.append((chat_id, msg_id))
        found = journaled.get(msg_id)
        if found is not None:
            rig.latest[(chat_id, msg_id)] = found
        return found

    rig.gw._reread = reread
    rig.gw._recall = recall

    def click(msg_id: int) -> ActionRequest:
        return ActionRequest(
            kind=ActionKind.CLICK,
            chat_id=GAME,
            message_id=msg_id,
            data="maze_left",
            expect=expect_text("x", timeout=0.05),
            expect_revision=3,
        )

    recalled = await rig.gw.submit(click(5))
    assert recalled.reason != "stale_button"
    assert [s.payload for s in rig.transport.sent] == ["maze_left"]
    assert recalls == [(GAME, 5)] and reads == []
    # Не нашлось в журнале — текущая версия перечитывается из Telegram, как после рестарта.
    missing = await rig.gw.submit(click(6))
    assert (missing.status, missing.reason) == (ActionStatus.REJECTED, "stale_button")
    assert recalls == [(GAME, 5), (GAME, 6)] and reads == [(GAME, 6)]


async def _features(rig: Rig, **flags: bool) -> None:
    await rig.settings.update(
        lambda s: s.model_copy(update={"features": s.features.model_copy(update=flags)}),
        changed_by="test",
    )


def _gadget_send(text: str, scenario: str | None, **kw: Any) -> ActionRequest:
    kw.setdefault("source", Source.SCENARIO)
    return send(text, scenario=scenario, expect=expect_text("Готово"), **kw)


async def test_buy_from_gadget_buy_scenario_passes_without_confirm(rig: Rig) -> None:
    await _features(rig, gadgets_buy=True)
    rig.reply_with("Готово")
    for text in ("/buy_right1", "/wear_12_p1"):
        res = await rig.gw.submit(_gadget_send(text, "gadget_buy"))
        assert res.status is ActionStatus.CONFIRMED
    unwear = await rig.gw.submit(_gadget_send("/unwear_p1", "gadget_buy"))
    assert (unwear.status, unwear.reason) == (ActionStatus.REJECTED, "risky_requires_confirm")
    assert [s.payload for s in rig.transport.sent] == ["/buy_right1", "/wear_12_p1"]


async def test_buy_from_manual_scenario_run_needs_confirm(rig: Rig) -> None:
    # Ручной запуск сценария идёт от MANUAL: без токена — как любой risky.
    await _features(rig, gadgets_buy=True)
    res = await rig.gw.submit(_gadget_send("/buy_right1", "gadget_buy", source=Source.MANUAL))
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "risky_requires_confirm")
    assert rig.transport.sent == []


async def test_buy_with_feature_off_rejected(rig: Rig) -> None:
    res = await rig.gw.submit(_gadget_send("/buy_right1", "gadget_buy"))
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "feature_off:gadgets_buy")
    assert rig.transport.sent == []


@pytest.mark.parametrize(
    ("text", "scenario", "source"),
    [
        ("/wear_3_p1", "deed:walk", Source.SCENARIO),
        ("/wear_3_p1", None, Source.SCENARIO),
        ("/wear_3_p1", "gadget_wear_set", Source.PLANNER),
        ("/buy_right1", "gadget_wear_set", Source.SCENARIO),
        ("/buy_right1", "gadget_upgrade", Source.SCENARIO),
        ("/unwear_p1", "gadget_wear_set", Source.SCENARIO),
    ],
)
async def test_wear_from_other_scenario_needs_confirm(
    rig: Rig, text: str, scenario: str | None, source: Source
) -> None:
    await _features(rig, gadgets_buy=True)
    res = await rig.gw.submit(_gadget_send(text, scenario, source=source))
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "risky_requires_confirm")
    assert rig.transport.sent == []


async def test_wear_from_wear_set_scenario_passes(rig: Rig) -> None:
    await _features(rig, gadgets_buy=True)
    rig.reply_with("Готово")
    res = await rig.gw.submit(_gadget_send("/wear_3_p1", "gadget_wear_set"))
    assert res.status is ActionStatus.CONFIRMED


async def _upgrade_task(rig: Rig, **task: Any) -> None:
    section = GadgetUpgradeSection.model_validate(task)
    await rig.settings.update(
        lambda s: s.model_copy(update={"gadget_upgrade": section}), changed_by="test"
    )


def _up_click(
    rig: Rig,
    data: str = "up_right_low",
    *,
    task_id: int | None = 2,
    revision: bool = True,
    content: bool = True,
    **kw: Any,
) -> ActionRequest:
    button = (Button("⚪️", 0, 0, data=data),)
    msg = make_msg("Апгрейд 📱Китайская мобила", msg_id=88, revision=3, buttons=button)
    rig.latest[(GAME, 88)] = msg
    return ActionRequest(
        kind=ActionKind.CLICK,
        chat_id=GAME,
        message_id=88,
        data=data,
        source=kw.pop("source", Source.SCENARIO),
        scenario=kw.pop("scenario", "gadget_upgrade"),
        task_id=task_id,
        expect_revision=3 if revision else None,
        expect_content=msg.content_hash() if content else None,
        expect=expect_text("Успех"),
        **kw,
    )


ACTIVE = {"status": "active", "task_id": 2, "slot": "right"}


@pytest.mark.parametrize(
    ("task", "data", "task_id"),
    [
        ({}, "up_right_low", 2),
        ({**ACTIVE, "status": "stopped"}, "up_right_low", 2),
        ({**ACTIVE, "status": "done"}, "up_right_high_1_accept", 2),
        ({**ACTIVE, "slot": "left"}, "up_right_low", 2),
        (ACTIVE, "up_left_low", 2),
        (ACTIVE, "up_right_low", 1),
        (ACTIVE, "up_right_low", None),
    ],
)
async def test_upgrade_click_needs_active_task_slot_and_task_id(
    rig: Rig, task: dict[str, Any], data: str, task_id: int | None
) -> None:
    await _upgrade_task(rig, **task)
    res = await rig.gw.submit(_up_click(rig, data, task_id=task_id))
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "upgrade_task_changed")
    assert rig.transport.sent == []


@pytest.mark.parametrize("data", ["up_right_low", "up_right_middle_1_accept"])
async def test_upgrade_click_of_active_task_passes(rig: Rig, data: str) -> None:
    await _upgrade_task(rig, **ACTIVE)
    rig.reply_with("Успех")
    res = await rig.gw.submit(_up_click(rig, data))
    assert res.status is ActionStatus.CONFIRMED
    assert [s.payload for s in rig.transport.sent] == [data]


@pytest.mark.parametrize(
    ("source", "scenario"),
    [
        (Source.MANUAL, "gadget_upgrade"),
        (Source.PLANNER, "gadget_upgrade"),
        (Source.SCENARIO, "gadget_buy"),
        (Source.SCENARIO, None),
    ],
)
async def test_upgrade_click_outside_its_scenario_needs_confirm(
    rig: Rig, source: Source, scenario: str | None
) -> None:
    await _upgrade_task(rig, **ACTIVE)
    res = await rig.gw.submit(_up_click(rig, source=source, scenario=scenario))
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "risky_requires_confirm")


@pytest.mark.parametrize(("revision", "content"), [(False, True), (True, False), (False, False)])
async def test_upgrade_click_without_frame_rejected(
    rig: Rig, revision: bool, content: bool
) -> None:
    await _upgrade_task(rig, **ACTIVE)
    res = await rig.gw.submit(_up_click(rig, revision=revision, content=content))
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "stale_frame_required")
    assert rig.transport.sent == []


async def test_upgrade_decline_is_nav(rig: Rig) -> None:
    rig.reply_with("Успех")
    decline = _up_click(rig, "up_right_low_decline", revision=False, content=False)
    res = await rig.gw.submit(decline)
    assert res.status is ActionStatus.CONFIRMED


async def test_stop_rejects_next_click_of_old_batch(rig: Rig) -> None:
    await _upgrade_task(rig, **{**ACTIVE, "task_id": 1})
    rig.reply_with("Успех")
    first = await rig.gw.submit(_up_click(rig, task_id=1))
    assert first.status is ActionStatus.CONFIRMED
    await _upgrade_task(rig, **{**ACTIVE, "task_id": 1, "status": "stopped"})
    second = await rig.gw.submit(_up_click(rig, task_id=1))
    assert (second.status, second.reason) == (ActionStatus.REJECTED, "upgrade_task_changed")
    assert [s.payload for s in rig.transport.sent] == ["up_right_low"]


async def test_stop_while_click_queued_rejects_it(rig: Rig) -> None:
    # Клик ждал очереди, а задачу остановили: перед отправкой — по текущей задаче.
    await _upgrade_task(rig, **ACTIVE)
    lease = await rig.gw.acquire_lease("other")
    pending = asyncio.create_task(rig.gw.submit(_up_click(rig)))
    await asyncio.sleep(0.02)
    await _upgrade_task(rig, **{**ACTIVE, "status": "stopped"})
    await rig.gw.release_lease(lease)
    res = await pending
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "upgrade_task_changed")
    assert rig.transport.sent == []


async def test_sells_from_gadget_buy_ignores_stocks_dump_flag(rig: Rig) -> None:
    await _features(rig, stocks_dump=False, gadgets_buy=True)
    rig.reply_with("Готово")
    res = await rig.gw.submit(_gadget_send("/sells_hooli_5", "gadget_buy"))
    assert res.status is ActionStatus.CONFIRMED
    dump = await rig.gw.submit(_gadget_send("/sells_hooli_5", "stocks_dump"))
    assert (dump.status, dump.reason) == (ActionStatus.REJECTED, "feature_off:stocks_dump")
    # Своя компания — по-прежнему только вручную с подтверждением.
    own = await rig.gw.submit(_gadget_send("/sells_bmesa_5", "gadget_buy"))
    assert (own.status, own.reason) == (ActionStatus.REJECTED, "risky_requires_confirm")
    await _features(rig, stocks_dump=True, gadgets_buy=False)
    off = await rig.gw.submit(_gadget_send("/sells_hooli_5", "gadget_buy"))
    assert (off.status, off.reason) == (ActionStatus.REJECTED, "feature_off:gadgets_buy")
    assert [s.payload for s in rig.transport.sent] == ["/sells_hooli_5"]


def test_sells_counts_as_gadget_buy_only_from_scenario_step() -> None:
    assert command_feature(_gadget_send("/sells_hooli_5", "gadget_buy")) == "gadgets_buy"
    manual = _gadget_send("/sells_hooli_5", "gadget_buy", source=Source.MANUAL)
    assert command_feature(manual) == "stocks_dump"


async def test_deadline_passed_rejects(rig: Rig) -> None:
    await _features(rig, gadgets_buy=True)
    past = datetime.now(UTC) - timedelta(seconds=1)
    res = await rig.gw.submit(_gadget_send("/buy_right1", "gadget_buy", deadline=past))
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "deadline")
    assert rig.transport.sent == []


def _main(scenario: str | None, source: Source = Source.SCENARIO, **kw: Any) -> ActionRequest:
    kw.setdefault("expect", expect_text("Битва"))
    return send("/main", source=source, scenario=scenario, **kw)


async def test_main_from_metro_scenario_passes_without_confirm(rig: Rig) -> None:
    rig.reply_with("Битва через 4ч. 21 мин.!")
    res = await rig.gw.submit(_main("metro"))
    assert res.status is ActionStatus.CONFIRMED
    assert [s.payload for s in rig.transport.sent] == ["/main"]


@pytest.mark.parametrize(
    ("scenario", "source"),
    [
        ("metro", Source.PLANNER),
        ("metro", Source.MANUAL),
        ("metro", Source.URGENT),
        (None, Source.PLANNER),
        (None, Source.SCENARIO),
        ("gadget_buy", Source.SCENARIO),
        ("refresh", Source.PLANNER),
    ],
)
async def test_main_elsewhere_needs_confirm(
    rig: Rig, scenario: str | None, source: Source
) -> None:
    res = await rig.gw.submit(_main(scenario, source))
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "risky_requires_confirm")
    assert rig.transport.sent == []


async def test_main_by_hand_with_confirmation_passes(rig: Rig) -> None:
    rig.reply_with("Битва через 4ч. 21 мин.!")
    res = await rig.gw.submit(_main(None, Source.MANUAL, risky_confirmed=True))
    assert res.status is ActionStatus.CONFIRMED


async def test_main_from_metro_scenario_passes_spending_block(rig: Rig) -> None:
    rig.gw.block_spending(RECONCILE_REASON)
    rig.reply_with("Битва через 4ч. 21 мин.!")
    res = await rig.gw.submit(_main("metro"))
    assert res.status is ActionStatus.CONFIRMED
    assert [s.payload for s in rig.transport.sent] == ["/main"]


async def test_main_uncertain_does_not_block_spending(rig: Rig) -> None:
    res = await rig.gw.submit(_main("metro", expect=Expectation(lambda d: None, 0.05)))
    assert res.status is ActionStatus.OUTCOME_UNKNOWN
    assert rig.gw.spending_blocked is None
