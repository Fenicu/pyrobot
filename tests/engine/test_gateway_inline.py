"""Шлюз и встреча на ночной прогулке: клик «⚔Драться» и приглашение через инлайн-режим бота."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.engine.commands import CommandClass
from app.engine.gateway.gateway import BULLS_WALK, RECONCILE_REASON, command_class
from app.engine.gateway.types import (
    ActionKind,
    ActionRequest,
    ActionStatus,
    Expectation,
    Source,
)
from app.engine.settings import Settings
from app.engine.types import Button
from tests.engine.gateway_rig import LIVE, Rig, expect_text, running_rig
from tests.engine.helpers import GAME, make_msg

INVITES = -1001149209877
INVITES_LIVE = Settings(
    engine=LIVE.engine, chats=LIVE.chats.model_copy(update={"bulls_invite_chat_id": INVITES})
)
OFFER = 3629542
CODE = "join_fight_GXnJJ0QNK2K"


def invite(**kw: Any) -> ActionRequest:
    fields: dict[str, Any] = {
        "kind": ActionKind.INLINE,
        "chat_id": INVITES,
        "text": CODE,
        "source": Source.URGENT,
        "scenario": BULLS_WALK,
        "idempotency_key": f"bulls_share:{OFFER}",
        **kw,
    }
    return ActionRequest(**fields)


def accept(**kw: Any) -> ActionRequest:
    fields: dict[str, Any] = {
        "kind": ActionKind.CLICK,
        "chat_id": GAME,
        "message_id": OFFER,
        "data": "fight_accept",
        "source": Source.URGENT,
        "scenario": BULLS_WALK,
        "expect": expect_text("Драка"),
        **kw,
    }
    return ActionRequest(**fields)


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    async for r in running_rig(INVITES_LIVE):
        r.latest[(GAME, OFFER)] = make_msg(
            "Гуляя по ночному городу…",
            msg_id=OFFER,
            buttons=(
                Button("⚔Драться", 0, 0, "fight_accept"),
                Button("🚶Пропустить", 0, 1, "fight_decline"),
            ),
        )
        yield r


async def _update(rig: Rig, section: str, **values: object) -> None:
    await rig.settings.update(
        lambda s: s.model_copy(update={section: getattr(s, section).model_copy(update=values)}),
        changed_by="test",
    )


def test_inline_is_its_own_class() -> None:
    assert command_class(invite()) is CommandClass.INLINE


async def test_invite_posted_once_and_message_id_kept(rig: Rig) -> None:
    res = await rig.gw.submit(invite())
    assert res.status is ActionStatus.CONFIRMED and res.reason == "sent"
    [sent] = rig.transport.sent
    assert (sent.kind, sent.chat_id, sent.payload) == ("inline", INVITES, CODE)
    assert res.answer is not None and int(res.answer) > 0
    assert rig.store.rows[res.action_id or 0].cls is CommandClass.INLINE
    again = await rig.gw.submit(invite())
    assert again.action_id == res.action_id and len(rig.transport.sent) == 1


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"chat_id": GAME}, "invite_chat_changed"),
        ({"text": ""}, "inline_invalid"),
        ({"scenario": None}, "bulls_walk_only"),
        ({"source": Source.MANUAL}, "bulls_walk_only"),
    ],
)
async def test_invite_only_from_walk_reaction_into_invite_chat(
    rig: Rig, kw: dict[str, Any], reason: str
) -> None:
    res = await rig.gw.submit(invite(**kw))
    assert (res.status, res.reason) == (ActionStatus.REJECTED, reason)
    assert rig.transport.sent == []


async def test_invite_chat_off_rejects(rig: Rig) -> None:
    await _update(rig, "chats", bulls_invite_chat_id=None)
    res = await rig.gw.submit(invite())
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "invite_chat_off")


async def test_invite_under_bulls_flag(rig: Rig) -> None:
    await _update(rig, "features", bulls=False)
    res = await rig.gw.submit(invite())
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "feature_off:bulls")


async def test_invite_dry_run_and_kill(rig: Rig) -> None:
    await rig.gw.kill("test")
    res = await rig.gw.submit(invite())
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "kill_switch")
    await rig.gw.unkill()
    await _update(rig, "engine", mode="dry_run")
    res = await rig.gw.submit(invite())
    assert (res.status, res.reason) == (ActionStatus.SUPPRESSED, "dry_run")
    assert rig.transport.sent == []


async def test_invite_spends_nothing(rig: Rig) -> None:
    rig.gw.block_spending(RECONCILE_REASON)
    res = await rig.gw.submit(invite())
    assert res.status is ActionStatus.CONFIRMED
    await rig.gw.allow_spending()
    rig.transport.fail_with.append(OSError("network"))
    lost = await rig.gw.submit(invite(idempotency_key="bulls_share:2"))
    assert lost.status is ActionStatus.OUTCOME_UNKNOWN
    assert rig.gw.spending_blocked is None


async def test_invite_does_not_wait_for_scenario_lease(rig: Rig) -> None:
    # Приглашение экран игры не трогает: шагам сценария под арендой не мешает.
    lease = await rig.gw.acquire_lease("scenario")
    res = await rig.gw.submit(invite())
    assert res.status is ActionStatus.CONFIRMED
    await rig.gw.release_lease(lease)


async def test_fight_accept_from_walk_reaction(rig: Rig) -> None:
    rig.reply_with("Драка")
    res = await rig.gw.submit(accept())
    assert res.status is ActionStatus.CONFIRMED
    assert [s.payload for s in rig.transport.sent] == ["fight_accept"]


@pytest.mark.parametrize(
    ("source", "scenario"),
    [
        (Source.PLANNER, None),
        (Source.SCENARIO, "bulls_join"),
        (Source.MANUAL, None),
        (Source.URGENT, None),
        (Source.SCENARIO, BULLS_WALK),
    ],
)
async def test_fight_accept_only_from_walk_reaction(
    rig: Rig, source: Source, scenario: str | None
) -> None:
    res = await rig.gw.submit(accept(source=source, scenario=scenario))
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "bulls_walk_only")
    assert rig.transport.sent == []


async def test_fight_accept_under_bulls_flag(rig: Rig) -> None:
    await _update(rig, "features", bulls=False)
    res = await rig.gw.submit(accept())
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "feature_off:bulls")


async def test_fight_decline_forbidden(rig: Rig) -> None:
    res = await rig.gw.submit(
        accept(data="fight_decline", expect=Expectation(lambda d: None, 0.05))
    )
    assert (res.status, res.reason) == (ActionStatus.REJECTED, "forbidden")
