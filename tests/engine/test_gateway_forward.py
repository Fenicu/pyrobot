"""Команда шлюза `forward`: пересылка сообщения игры в чат команды со своей политикой."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.engine.commands import CommandClass
from app.engine.gateway.gateway import RECONCILE_REASON, command_class
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus, Source
from app.engine.settings import Settings
from app.engine.types import IncomingMessage
from tests.engine.gateway_rig import LIVE, Rig, expect_text, running_rig, send
from tests.engine.helpers import GAME, make_msg, until

TEAM = -1001149209877
TEAM_LIVE = Settings(
    engine=LIVE.engine, chats=LIVE.chats.model_copy(update={"team_chat_id": TEAM})
)


def source(
    msg_id: int = 5, text: str = "Ты завершил задание в команде и заработал 90🏆"
) -> IncomingMessage:
    return make_msg(text, msg_id=msg_id)


def forward(msg_id: int = 5, **kw: object) -> ActionRequest:
    fields: dict[str, object] = {
        "kind": ActionKind.FORWARD,
        "chat_id": TEAM,
        "from_chat_id": GAME,
        "message_id": msg_id,
        "idempotency_key": f"forward:{GAME}:{msg_id}",
        "expect_content": source(msg_id).content_hash(),
        **kw,
    }
    return ActionRequest(**fields)  # type: ignore[arg-type]


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    async for r in running_rig(TEAM_LIVE):
        for msg_id in (5, 6, 7):
            r.transport.messages[(GAME, msg_id)] = source(msg_id)
        yield r


async def _engine(rig: Rig, **update: object) -> None:
    await rig.settings.update(
        lambda s: s.model_copy(update={"engine": s.engine.model_copy(update=update)}),
        changed_by="test",
    )


async def _team(rig: Rig, chat: int | None) -> None:
    await rig.settings.update(
        lambda s: s.model_copy(
            update={"chats": s.chats.model_copy(update={"team_chat_id": chat})}
        ),
        changed_by="test",
    )


def test_forward_is_its_own_class() -> None:
    assert command_class(forward()) is CommandClass.FORWARD


async def test_forward_sent_once_and_destination_id_kept(rig: Rig) -> None:
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.CONFIRMED and res.reason == "sent"
    [sent] = rig.transport.sent
    assert (sent.kind, sent.chat_id, sent.payload, sent.message_id) == (
        "forward",
        TEAM,
        str(GAME),
        5,
    )
    row = rig.store.rows[res.action_id or 0]
    assert row.cls is CommandClass.FORWARD
    assert row.answer == res.answer and res.answer is not None and int(res.answer) > 0
    again = await rig.gw.submit(forward())
    assert again.status is ActionStatus.CONFIRMED and again.action_id == res.action_id
    assert len(rig.transport.sent) == 1


async def test_dry_run_suppresses_and_records() -> None:
    dry = TEAM_LIVE.model_copy(
        update={"engine": LIVE.engine.model_copy(update={"mode": "dry_run"})}
    )
    async for r in running_rig(dry):
        res = await r.gw.submit(forward())
        assert res.status is ActionStatus.SUPPRESSED and res.reason == "dry_run"
        assert r.transport.sent == []
        assert [row.status for row in r.store.rows.values()] == [ActionStatus.SUPPRESSED]


async def test_kill_rejects(rig: Rig) -> None:
    await rig.gw.kill("test")
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.REJECTED and res.reason == "kill_switch"
    assert rig.transport.sent == []


async def test_kill_setting_rejects(rig: Rig) -> None:
    await _engine(rig, killed=True)
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.REJECTED and res.reason == "kill_switch"


async def test_pause_does_not_stop_forward(rig: Rig) -> None:
    await _engine(rig, paused=True, urgent_while_paused=False, manual_while_paused=False)
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.CONFIRMED
    assert len(rig.transport.sent) == 1


async def test_team_chat_off_rejects(rig: Rig) -> None:
    await _team(rig, None)
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.REJECTED and res.reason == "team_chat_off"
    assert rig.transport.sent == []


async def test_only_from_game_chat(rig: Rig) -> None:
    res = await rig.gw.submit(forward(from_chat_id=-1001109615116))
    assert res.status is ActionStatus.REJECTED and res.reason == "forward_source"
    assert rig.transport.sent == []


async def test_target_chat_checked_again_at_execution() -> None:
    rig = Rig(TEAM_LIVE)
    pending = asyncio.ensure_future(rig.gw.submit(forward()))
    await until(lambda: rig.gw.queue_size == 1)
    await _team(rig, -1002222222222)
    rig.start()
    try:
        res = await asyncio.wait_for(pending, 1)
    finally:
        await rig.stop()
    assert res.status is ActionStatus.REJECTED and res.reason == "team_chat_changed"
    assert rig.transport.sent == []


async def test_unknown_outcome_not_retried_and_does_not_block_spending(rig: Rig) -> None:
    hooked: list[int | None] = []
    rig.gw.on_uncertain = lambda req, action_id: hooked.append(action_id)
    rig.transport.fail_with.append(TimeoutError("request timed out"))
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.OUTCOME_UNKNOWN and res.reason.startswith("send_error")
    assert rig.transport.sent == []
    assert rig.gw.spending_blocked is None and hooked == []
    again = await rig.gw.submit(forward())
    assert again.status is ActionStatus.OUTCOME_UNKNOWN and again.action_id == res.action_id
    assert rig.transport.sent == []
    assert await rig.store.unreconciled() == []


async def test_spend_block_does_not_hold_forward(rig: Rig) -> None:
    rig.gw.block_spending(RECONCILE_REASON)
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.CONFIRMED


async def test_forward_passes_scenario_lease(rig: Rig) -> None:
    lease = await rig.gw.acquire_lease("metro")
    try:
        res = await asyncio.wait_for(rig.gw.submit(forward()), 1)
    finally:
        await rig.gw.release_lease(lease)
    assert res.status is ActionStatus.CONFIRMED


async def test_forward_waits_for_request_interval(rig: Rig) -> None:
    await _engine(rig, min_request_interval_s=0.2)
    rig.reply_with("Ты отправился работать")
    await rig.gw.submit(send("/job", expect=expect_text("работать"), source=Source.URGENT))
    await rig.gw.submit(forward())
    job, fwd = rig.transport.sent
    assert fwd.at - job.at >= 0.19


async def test_team_chat_checked_once_before_first_forward(rig: Rig) -> None:
    assert (await rig.gw.submit(forward(5))).status is ActionStatus.CONFIRMED
    assert (await rig.gw.submit(forward(6))).status is ActionStatus.CONFIRMED
    assert rig.transport.group_checks == [TEAM]
    other = -1002222222222
    await _team(rig, other)
    await rig.gw.submit(forward(7, chat_id=other))
    assert rig.transport.group_checks == [TEAM, other]


@pytest.mark.parametrize("verdict", ["not_group", "not_member", "unavailable"])
async def test_unverified_team_chat_refused_and_rechecked(rig: Rig, verdict: str) -> None:
    rig.transport.groups[TEAM] = verdict
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.REFUSED and res.reason == f"team_chat_{verdict}"
    assert rig.transport.sent == []
    # Отказ не кешируется и ключ не расходует: аккаунт добавили в группу — следующая уходит.
    rig.transport.groups[TEAM] = "ok"
    assert (await rig.gw.submit(forward())).status is ActionStatus.CONFIRMED
    assert rig.transport.group_checks == [TEAM, TEAM]


async def test_team_chat_check_error_is_refusal(rig: Rig) -> None:
    rig.transport.group_error = OSError("network down")
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.REFUSED and res.reason == "team_chat_unavailable"
    assert rig.transport.sent == []


async def test_source_reread_right_before_forward(rig: Rig) -> None:
    assert (await rig.gw.submit(forward())).status is ActionStatus.CONFIRMED
    assert rig.transport.fetches == [(GAME, 5)]


async def test_edited_source_refused(rig: Rig) -> None:
    # Игра поправила сообщение после того, как реакция его увидела: пересылается не оно.
    rig.transport.messages[(GAME, 5)] = source(5, "Ты завершил задание в команде и заработал 0🏆")
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.REFUSED and res.reason == "source_changed"
    assert rig.transport.sent == []
    assert rig.store.rows[res.action_id or 0].status is ActionStatus.REFUSED


async def test_deleted_source_refused(rig: Rig) -> None:
    del rig.transport.messages[(GAME, 5)]
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.REFUSED and res.reason == "source_gone"
    assert rig.transport.sent == []


async def test_unreadable_source_refused(rig: Rig) -> None:
    rig.transport.fetch_fail_with.append(OSError("network down"))
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.REFUSED and res.reason == "source_unreadable"
    assert rig.transport.sent == []
    assert rig.gw.spending_blocked is None


async def test_forward_without_seen_content_rejected(rig: Rig) -> None:
    res = await rig.gw.submit(forward(expect_content=None))
    assert res.status is ActionStatus.REJECTED and res.reason == "forward_invalid"
    assert rig.transport.sent == [] and rig.transport.fetches == []


async def test_flood_wait_on_reread_waits_and_reads_again(rig: Rig) -> None:
    from app.engine.transport.base import FloodWait

    rig.transport.fetch_fail_with.append(FloodWait(0.05))
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.CONFIRMED
    assert rig.transport.fetches == [(GAME, 5), (GAME, 5)]
    assert len(rig.transport.sent) == 1


async def test_team_chat_rechecked_after_switch_back(rig: Rig) -> None:
    # A → B → A: проверка A не переживает смену настройки — группу могли покинуть, чат сменить.
    other = -1002222222222
    assert (await rig.gw.submit(forward(5))).status is ActionStatus.CONFIRMED
    await _team(rig, other)
    assert (await rig.gw.submit(forward(6, chat_id=other))).status is ActionStatus.CONFIRMED
    await _team(rig, TEAM)
    assert (await rig.gw.submit(forward(7))).status is ActionStatus.CONFIRMED
    assert rig.transport.group_checks == [TEAM, other, TEAM]


async def test_team_chat_changed_during_source_read_not_forwarded(rig: Rig) -> None:
    async def switch() -> None:
        await _team(rig, -1002222222222)

    rig.transport.on_fetch = switch
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.REJECTED and res.reason == "team_chat_changed"
    assert rig.transport.fetches == [(GAME, 5)] and rig.transport.sent == []


async def test_kill_during_source_read_not_forwarded(rig: Rig) -> None:
    async def kill() -> None:
        await rig.gw.kill("test")

    rig.transport.on_fetch = kill
    res = await rig.gw.submit(forward())
    assert res.status is ActionStatus.REJECTED and res.reason == "kill_switch"
    assert rig.transport.sent == []
