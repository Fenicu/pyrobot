from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.db.actions import DbActionStore
from app.db.base import Database
from app.db.models import ActionRow, NotificationRow
from app.db.planner import DbPlannerStore
from app.engine.commands import CommandClass
from app.engine.gateway.store import Closed, DuplicateKey, Obligation
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus, Source
from app.engine.planner.types import Wait

pytestmark = pytest.mark.db
T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


async def _notes(db: Database) -> list[NotificationRow]:
    async with db.sessions() as session:
        return list(await session.scalars(select(NotificationRow).order_by(NotificationRow.id)))


def _forward(msg_id: int) -> ActionRequest:
    return ActionRequest(
        kind=ActionKind.FORWARD,
        chat_id=-1001149209877,
        from_chat_id=227859379,
        message_id=msg_id,
        idempotency_key=f"forward:227859379:{msg_id}",
        expect_content="h",
    )


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


async def test_update_keeps_final_command_class(clean_db: Database) -> None:
    # Класс команды акций шлюз уточняет перед каждой попыткой: итог пишется с ним.
    store = DbActionStore(clean_db, account_id=1)
    req = ActionRequest(kind=ActionKind.SEND, chat_id=1, text="/buys_stark_5")
    action_id = await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    await store.update(action_id, status=ActionStatus.SENT, sent=True)
    await store.update(
        action_id,
        status=ActionStatus.REJECTED,
        reason="risky_requires_confirm",
        cls=CommandClass.RISKY,
    )
    async with clean_db.sessions() as session:
        row = await session.get(ActionRow, action_id)
    assert row is not None
    assert (row.command_class, row.status) == ("risky", "rejected")


async def test_mark_unfinished_unknown(clean_db: Database) -> None:
    store = DbActionStore(clean_db, account_id=1)
    req = ActionRequest(kind=ActionKind.SEND, chat_id=1, text="/job")
    a = await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    b = await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    await store.update(b, status=ActionStatus.SENT, sent=True)
    c = await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    await store.update(c, status=ActionStatus.CONFIRMED)
    closed = await store.mark_unfinished_unknown()
    assert sorted(c.action_id for c in closed) == [a, b]
    assert {c.cls for c in closed} == {CommandClass.ACTION}


async def test_mark_unfinished_includes_cancelled_unknown(clean_db: Database) -> None:
    store = DbActionStore(clean_db, account_id=1)
    req = ActionRequest(kind=ActionKind.SEND, chat_id=1, text="/job")
    cancelled = await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    await store.update(cancelled, status=ActionStatus.OUTCOME_UNKNOWN, reason="cancelled")
    timeout = await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    await store.update(timeout, status=ActionStatus.OUTCOME_UNKNOWN, reason="timeout")
    assert [c.action_id for c in await store.mark_unfinished_unknown()] == [cancelled]
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
    assert sorted(c.action_id for c in await store.mark_unfinished_unknown()) == [spend, nav]
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
    assert await store.mark_unfinished_unknown() == [Closed(forwarded, CommandClass.FORWARD, 5)]
    [note] = await _notes(clean_db)
    assert (note.level, note.code) == ("warn", "team_forward_unknown")
    assert "forward 5 " in note.text and "restart" in note.text
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


async def test_lost_forward_notified_once_with_its_closing(clean_db: Database) -> None:
    # Уведомление — в той же транзакции, что и закрытие: повторный старт строку уже не берёт и
    # второго уведомления не пишет; отмена посреди пересылки (cancelled) — тоже одно.
    store = DbActionStore(clean_db, account_id=1)
    await store.create(_forward(5), CommandClass.FORWARD, ActionStatus.SENT)
    cancelled = await store.create(_forward(6), CommandClass.FORWARD, ActionStatus.INTENT)
    await store.update(cancelled, status=ActionStatus.OUTCOME_UNKNOWN, reason="cancelled")
    job = ActionRequest(kind=ActionKind.SEND, chat_id=1, text="/job")
    await store.create(job, CommandClass.ACTION, ActionStatus.SENT)
    await store.mark_unfinished_unknown()
    await store.mark_unfinished_unknown()
    notes = await _notes(clean_db)
    assert [n.code for n in notes] == ["team_forward_unknown"] * 2
    assert {n.text.split()[1] for n in notes} == {"5", "6"}


async def test_lost_forward_not_closed_without_its_notification(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Запись уведомления сорвалась — закрытие откатывается вместе с ней: следующий старт возьмёт
    # строку снова, пропуска нет.
    import app.db.actions as actions

    store = DbActionStore(clean_db, account_id=1)
    forwarded = await store.create(_forward(5), CommandClass.FORWARD, ActionStatus.SENT)
    monkeypatch.setattr(actions, "LOST_FORWARD_CODE", "x" * 100)
    with pytest.raises(DBAPIError):
        await store.mark_unfinished_unknown()
    async with clean_db.sessions() as session:
        row = await session.get(ActionRow, forwarded)
    assert row is not None and row.status == ActionStatus.SENT.value
    assert await _notes(clean_db) == []
    monkeypatch.undo()
    assert [c.action_id for c in await store.mark_unfinished_unknown()] == [forwarded]
    assert [n.code for n in await _notes(clean_db)] == ["team_forward_unknown"]
