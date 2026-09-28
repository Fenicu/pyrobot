from datetime import UTC, datetime

import pytest

from app.db.actions import DbActionStore
from app.db.base import Database
from app.db.models import ActionRow
from app.db.planner import DbPlannerStore
from app.engine.commands import CommandClass
from app.engine.gateway.store import DuplicateKey, Obligation
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus, Source
from app.engine.planner.types import Wait

pytestmark = pytest.mark.db
T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


async def test_lifecycle_idempotency_and_duplicate(clean_db: Database) -> None:
    store = DbActionStore(clean_db, account_id=1)
    req = ActionRequest(
        kind=ActionKind.SEND, chat_id=1, text="/job", source=Source.MANUAL, idempotency_key="k1"
    )
    action_id = await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    await store.update(action_id, status=ActionStatus.SENT, attempts=1, sent=True, answer="t")
    await store.update(action_id, status=ActionStatus.CONFIRMED, reason="ok", match_detail="x")
    stored = await store.get_by_key("k1")
    assert stored is not None and stored.id == action_id
    result = stored.to_result()
    assert result.status is ActionStatus.CONFIRMED and result.answer == "t"
    assert result.match is not None and result.match.detail == "x"
    with pytest.raises(DuplicateKey) as dup:
        await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    assert dup.value.existing.id == action_id


async def test_mark_unfinished_unknown(clean_db: Database) -> None:
    store = DbActionStore(clean_db, account_id=1)
    req = ActionRequest(kind=ActionKind.SEND, chat_id=1, text="/job")
    a = await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    b = await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    await store.update(b, status=ActionStatus.SENT, sent=True)
    c = await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    await store.update(c, status=ActionStatus.CONFIRMED)
    assert sorted(await store.mark_unfinished_unknown()) == [a, b]


async def test_mark_unfinished_includes_cancelled_unknown(clean_db: Database) -> None:
    store = DbActionStore(clean_db, account_id=1)
    req = ActionRequest(kind=ActionKind.SEND, chat_id=1, text="/job")
    cancelled = await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    await store.update(cancelled, status=ActionStatus.OUTCOME_UNKNOWN, reason="cancelled")
    timeout = await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    await store.update(timeout, status=ActionStatus.OUTCOME_UNKNOWN, reason="timeout")
    assert await store.mark_unfinished_unknown() == [cancelled]
    assert await store.mark_unfinished_unknown() == []


async def test_obligations_survive_restart_until_reconciled(clean_db: Database) -> None:
    store = DbActionStore(clean_db, account_id=1)
    spend = await store.create(
        ActionRequest(kind=ActionKind.SEND, chat_id=1, text="/harvest"),
        CommandClass.ACTION,
        ActionStatus.SENT,
    )
    nav = await store.create(
        ActionRequest(kind=ActionKind.SEND, chat_id=1, text="/inv"),
        CommandClass.NAV,
        ActionStatus.SENT,
    )
    assert sorted(await store.mark_unfinished_unknown()) == [spend, nav]
    assert await store.unreconciled() == [Obligation(spend, "send", "/harvest", None)]
    again = DbActionStore(clean_db, account_id=1)
    assert [o.action_id for o in await again.unreconciled()] == [spend]
    await again.mark_reconciled([spend])
    assert await store.unreconciled() == []


async def test_forward_unknown_after_restart_is_not_an_obligation(clean_db: Database) -> None:
    store = DbActionStore(clean_db, account_id=1)
    req = ActionRequest(
        kind=ActionKind.FORWARD,
        chat_id=-1001149209877,
        from_chat_id=227859379,
        message_id=5,
        idempotency_key="forward:227859379:5",
    )
    forwarded = await store.create(req, CommandClass.FORWARD, ActionStatus.SENT)
    assert await store.mark_unfinished_unknown() == [forwarded]
    assert await store.unreconciled() == []
    async with clean_db.sessions() as session:
        row = await session.get(ActionRow, forwarded)
    assert row is not None and row.payload["from_chat_id"] == 227859379
    stored = await store.get_by_key("forward:227859379:5")
    assert stored is not None and stored.status is ActionStatus.OUTCOME_UNKNOWN


async def test_scenario_run_id_is_stored(clean_db: Database) -> None:
    planner = DbPlannerStore(clean_db, 1)
    decision = await planner.record(T0, Wait(None, "busy"))
    run_id = await planner.run_started(decision, "deed:job", {}, T0)
    store = DbActionStore(clean_db, account_id=1)
    step = ActionRequest(kind=ActionKind.SEND, chat_id=1, text="/job", scenario_run_id=run_id)
    own = await store.create(step, CommandClass.ACTION, ActionStatus.INTENT)
    manual = await store.create(
        ActionRequest(kind=ActionKind.SEND, chat_id=1, text="/inv", source=Source.MANUAL),
        CommandClass.NAV,
        ActionStatus.INTENT,
    )
    async with clean_db.sessions() as session:
        runs = {a: (await session.get(ActionRow, a)).scenario_run_id for a in (own, manual)}
    assert runs == {own: run_id, manual: None}
    # Идентификатор запуска не входит в отпечаток идемпотентности.
    assert "scenario_run_id" not in step.payload()
