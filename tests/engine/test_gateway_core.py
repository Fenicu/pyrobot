import asyncio
import time
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from app.engine.bus import Delivery
from app.engine.commands import CommandClass
from app.engine.events import AntiFlood
from app.engine.gateway.store import StoredAction
from app.engine.gateway.types import (
    ActionKind,
    ActionRequest,
    ActionStatus,
    Expectation,
    Match,
    Source,
    Verdict,
)
from app.engine.memory import MemoryActionStore
from app.engine.settings import Settings
from app.engine.transport.base import FloodWait
from app.engine.transport.fake import Sent
from app.engine.types import Button, IncomingMessage
from tests.engine.gateway_rig import LIVE, Rig, expect_text, running_rig, send
from tests.engine.helpers import GAME, make_msg, now, until


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


async def test_kill_cancelled_resolves_all_queued_futures() -> None:
    class SlowStore(MemoryActionStore):
        async def create(
            self, req: ActionRequest, cls: CommandClass, status: ActionStatus, reason: str = ""
        ) -> int:
            if status is ActionStatus.SUPPRESSED:
                await asyncio.sleep(0.05)
            return await super().create(req, cls, status, reason)

    rig = Rig()
    rig.store = SlowStore()
    rig.gw._store = rig.store  # type: ignore[attr-defined]
    subs = [asyncio.create_task(rig.gw.submit(send("😎Я"))) for _ in range(3)]
    await asyncio.sleep(0.01)
    k = asyncio.create_task(rig.gw.kill("x"))
    await asyncio.sleep(0.07)
    k.cancel()
    await asyncio.gather(k, return_exceptions=True)
    await asyncio.sleep(0.2)
    assert all(s.done() for s in subs)
    assert all(s.result().status is ActionStatus.SUPPRESSED for s in subs)
    assert rig.gw.queue_size == 0


async def test_check_failure_rejected_and_gateway_keeps_running(rig: Rig) -> None:
    def bad_latest(chat_id: int, message_id: int) -> IncomingMessage | None:
        raise KeyError("boom")

    rig.gw._latest = bad_latest  # type: ignore[attr-defined]
    click = ActionRequest(
        kind=ActionKind.CLICK, chat_id=GAME, message_id=1, data="maze_up", expect=expect_text("x")
    )
    res = await rig.gw.submit(click)
    assert res.status is ActionStatus.REJECTED and res.reason == "check_failed:KeyError"
    res2 = await rig.gw.submit(send("😎Я"))
    assert res2.status is ActionStatus.CONFIRMED


async def test_floodwait_pauses_whole_gateway(rig: Rig) -> None:
    rig.transport.fail_with = [FloodWait(0.2)]
    t0 = time.monotonic()
    res = await rig.gw.submit(send("😎Я", ttl_s=0.1))
    assert res.status is ActionStatus.REFUSED and res.reason.startswith("flood_wait:")
    res2 = await rig.gw.submit(send("😎Я"))
    assert res2.status is ActionStatus.CONFIRMED
    assert rig.transport.sent[-1].at - t0 >= 0.15


async def test_forbidden_resolves_immediately_behind_inflight(rig: Rig) -> None:
    slow = asyncio.create_task(rig.gw.submit(send("/job", expect=expect_text("zzz", timeout=1.0))))
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    res = await rig.gw.submit(send("/changecompany", expect=expect_text("x")))
    elapsed = time.monotonic() - t0
    assert res.status is ActionStatus.REJECTED and res.reason == "forbidden"
    assert elapsed < 0.1
    slow.cancel()
    await asyncio.gather(slow, return_exceptions=True)


async def test_submit_racing_shutdown_not_sent(rig: Rig) -> None:
    class SlowKeyStore(MemoryActionStore):
        async def get_by_key(self, key: str) -> StoredAction | None:
            await asyncio.sleep(0.05)
            return await super().get_by_key(key)

    rig.store = SlowKeyStore()
    rig.gw._store = rig.store  # type: ignore[attr-defined]
    t = asyncio.create_task(rig.gw.submit(send("😎Я", idempotency_key="z")))
    await asyncio.sleep(0.01)
    await rig.gw.shutdown()
    res = await t
    assert res.status is ActionStatus.SUPPRESSED and res.reason == "shutdown"
    assert rig.transport.sent == []


async def test_idempotency_key_reusable_after_dry_run_suppressed() -> None:
    dry = Settings(engine=LIVE.engine.model_copy(update={"mode": "dry_run"}))
    async for r in running_rig(dry):
        r1 = await r.gw.submit(send("/job", expect=expect_text("работать"), idempotency_key="k"))
        assert r1.status is ActionStatus.SUPPRESSED and r1.reason == "dry_run"
        await r.settings.update(
            lambda s: s.model_copy(
                update={"engine": s.engine.model_copy(update={"mode": "live"})}
            ),
            changed_by="t",
        )
        r.reply_with("Ты отправился работать")
        r2 = await r.gw.submit(send("/job", expect=expect_text("работать"), idempotency_key="k"))
        assert r2.status is ActionStatus.CONFIRMED
        assert len(r.transport.sent) == 1


async def test_idempotency_key_too_long_rejected(rig: Rig) -> None:
    res = await rig.gw.submit(send("😎Я", idempotency_key="x" * 101))
    assert res.status is ActionStatus.REJECTED and res.reason == "bad_key"
    assert rig.transport.sent == []


async def test_recheck_before_attempt_catches_kill_mid_flight() -> None:
    cfg = Settings(engine=LIVE.engine.model_copy(update={"min_request_interval_s": 0.2}))
    async for r in running_rig(cfg):
        t1 = asyncio.create_task(r.gw.submit(send("😎Я")))
        t2 = asyncio.create_task(r.gw.submit(send("😎Я")))
        await until(lambda sent=r.transport.sent: len(sent) == 1)
        await r.settings.update(
            lambda s: s.model_copy(
                update={"engine": s.engine.model_copy(update={"killed": True})}
            ),
            changed_by="t",
        )
        res1 = await t1
        res2 = await t2
        assert res1.status is ActionStatus.CONFIRMED
        assert res2.status is ActionStatus.SUPPRESSED and res2.reason == "kill_switch"
        assert len(r.transport.sent) == 1


async def test_expectation_default_timeout_from_settings(rig: Rig) -> None:
    def never_matches(d: Delivery) -> Match | None:
        return None

    res = await rig.gw.submit(send("/job", expect=Expectation(never_matches)))
    assert res.status is ActionStatus.OUTCOME_UNKNOWN and res.reason == "timeout"


async def test_antiflood_giveup_pauses_whole_gateway() -> None:
    cfg = Settings(
        engine=LIVE.engine.model_copy(update={"antiflood_retry_max": 0, "antiflood_pause_s": 0.2})
    )
    async for r in running_rig(cfg):

        async def responder(rec: Sent, rig: Rig = r) -> None:
            await rig.deliver(make_msg("flood", msg_id=900), events=(AntiFlood(),))

        r.transport.responder = responder
        t0 = time.monotonic()
        res = await r.gw.submit(send("/job", expect=expect_text("работать", timeout=0.3)))
        assert res.status is ActionStatus.OUTCOME_UNKNOWN and res.reason == "antiflood"
        res2 = await r.gw.submit(send("😎Я"))
        assert res2.status is ActionStatus.CONFIRMED
        assert r.transport.sent[-1].at - t0 >= 0.15


async def test_antiflood_retry_waits_between_attempts() -> None:
    cfg = Settings(
        engine=LIVE.engine.model_copy(update={"antiflood_retry_max": 1, "antiflood_pause_s": 0.2})
    )
    async for r in running_rig(cfg):
        calls = 0

        async def responder(rec: Sent, rig: Rig = r) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                await rig.deliver(make_msg("flood", msg_id=900), events=(AntiFlood(),))
            else:
                await rig.deliver(make_msg("Ты отправился работать"))

        r.transport.responder = responder
        res = await r.gw.submit(send("/job", expect=expect_text("работать", timeout=0.5)))
        assert res.status is ActionStatus.CONFIRMED
        assert len(r.transport.sent) == 2
        assert r.transport.sent[1].at - r.transport.sent[0].at >= 0.15


async def test_check_failure_at_enqueue_time_is_rejected_not_raised() -> None:
    async for r in running_rig():

        def bad_static_checks(req: ActionRequest, cls: CommandClass) -> None:
            raise RuntimeError("boom")

        r.gw._static_checks = bad_static_checks  # type: ignore[method-assign]
        res = await r.gw.submit(send("😎Я"))
        assert res.status is ActionStatus.REJECTED and res.reason == "check_failed:RuntimeError"
        assert r.transport.sent == []


class _ClosingCond:
    """Оборачивает реальный Condition: при входе в критическую секцию
    выставляет _closed=True — симулирует shutdown() ровно между
    enqueue-time проверкой и захватом блокировки в submit()."""

    def __init__(self, cond: asyncio.Condition, gw: object) -> None:
        self._cond = cond
        self._gw = gw

    async def __aenter__(self) -> None:
        await self._cond.acquire()
        self._gw._closed = True  # type: ignore[attr-defined]

    async def __aexit__(self, *exc: object) -> None:
        self._cond.release()


async def test_submit_closed_between_check_and_enqueue_not_sent() -> None:
    async for r in running_rig():
        r.gw._cond = _ClosingCond(r.gw._cond, r.gw)  # type: ignore[attr-defined]
        res = await r.gw.submit(send("😎Я"))
        assert res.status is ActionStatus.SUPPRESSED and res.reason == "shutdown"
        assert r.transport.sent == []
        assert r.gw.queue_size == 0


async def test_can_send_blocks_at_enqueue(rig: Rig) -> None:
    rig.block = "tg_offline"
    res = await rig.gw.submit(send("😎Я"))
    assert res.status is ActionStatus.REJECTED and res.reason == "tg_offline"
    assert rig.transport.sent == []
    rig.block = None
    assert (await rig.gw.submit(send("😎Я"))).status is ActionStatus.CONFIRMED


async def test_can_send_rechecked_before_attempt() -> None:
    cfg = Settings(engine=LIVE.engine.model_copy(update={"min_request_interval_s": 0.2}))
    async for r in running_rig(cfg):
        t1 = asyncio.create_task(r.gw.submit(send("😎Я")))
        t2 = asyncio.create_task(r.gw.submit(send("😎Я")))
        await until(lambda sent=r.transport.sent: len(sent) == 1)
        r.block = "lock_lost"
        assert (await t1).status is ActionStatus.CONFIRMED
        res2 = await t2
        assert res2.status is ActionStatus.REJECTED and res2.reason == "lock_lost"
        assert len(r.transport.sent) == 1
