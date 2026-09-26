import pytest

from app.db.actions import DbActionStore
from app.db.base import Database
from app.engine.commands import CommandClass
from app.engine.gateway.store import DuplicateKey
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus, Source

pytestmark = pytest.mark.db


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
