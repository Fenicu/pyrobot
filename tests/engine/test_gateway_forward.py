"""Команда шлюза `forward`: пересылка сообщения игры в чат команды со своей политикой."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.engine.commands import CommandClass
from app.engine.gateway.gateway import RECONCILE_REASON, command_class
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus, Source
from app.engine.settings import Settings
from tests.engine.gateway_rig import LIVE, Rig, expect_text, running_rig, send
from tests.engine.helpers import GAME, until

TEAM = -1001149209877
TEAM_LIVE = Settings(
    engine=LIVE.engine, chats=LIVE.chats.model_copy(update={"team_chat_id": TEAM})
)


def forward(msg_id: int = 5, **kw: object) -> ActionRequest:
    fields: dict[str, object] = {
        "kind": ActionKind.FORWARD,
        "chat_id": TEAM,
        "from_chat_id": GAME,
        "message_id": msg_id,
        "idempotency_key": f"forward:{GAME}:{msg_id}",
        **kw,
    }
    return ActionRequest(**fields)  # type: ignore[arg-type]


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    async for r in running_rig(TEAM_LIVE):
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
    await _team(rig, -1009999)
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
