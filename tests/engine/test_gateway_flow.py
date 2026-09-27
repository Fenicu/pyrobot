import asyncio
from collections.abc import AsyncIterator

import pytest

from app.engine.events import AntiFlood
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus, Source
from app.engine.settings import Settings
from app.engine.transport.base import FloodWait, TransportAuthLost, TransportRejected
from app.engine.transport.fake import Sent
from app.engine.types import Button
from tests.engine.gateway_rig import GAME, LIVE, Rig, expect_text, running_rig, send
from tests.engine.helpers import make_msg, until


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    async for r in running_rig():
        yield r


FLOOD = "Ты шлёшь запросы к боту слишком часто. Полегче, йоу."


async def test_antiflood_retry_then_confirm(rig: Rig) -> None:
    calls = 0

    async def responder(rec: Sent) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            await rig.deliver(make_msg(FLOOD), (AntiFlood(),))
        else:
            await rig.deliver(make_msg("Ты отправился работать"))

    rig.transport.responder = responder
    res = await rig.gw.submit(send("/job", expect=expect_text("работать")))
    assert res.status is ActionStatus.CONFIRMED and len(rig.transport.sent) == 2


async def test_antiflood_exhausted_is_unknown(rig: Rig) -> None:
    async def responder(rec: Sent) -> None:
        await rig.deliver(make_msg(FLOOD), (AntiFlood(),))

    rig.transport.responder = responder
    res = await rig.gw.submit(send("/job", expect=expect_text("работать")))
    assert res.status is ActionStatus.OUTCOME_UNKNOWN and res.reason == "antiflood"
    assert len(rig.transport.sent) == 1 + LIVE.engine.antiflood_retry_max


async def test_old_antiflood_does_not_trigger_retry(rig: Rig) -> None:
    async def responder(rec: Sent) -> None:
        await rig.deliver(make_msg(FLOOD), (AntiFlood(),), journal_id=0)

    rig.transport.responder = responder
    res = await rig.gw.submit(send("/job", expect=expect_text("работать", timeout=0.1)))
    assert res.reason == "timeout" and len(rig.transport.sent) == 1


async def test_flood_wait_sleeps_and_retries(rig: Rig) -> None:
    rig.transport.fail_with.append(FloodWait(0.05))
    assert (await rig.gw.submit(send("😎Я"))).status is ActionStatus.CONFIRMED
    assert len(rig.transport.sent) == 1


async def test_transport_errors_classified(rig: Rig) -> None:
    rig.transport.fail_with.append(ConnectionError("down"))
    res = await rig.gw.submit(send("😎Я"))
    assert res.status is ActionStatus.OUTCOME_UNKNOWN
    assert res.reason == "send_error:ConnectionError"
    rig.transport.fail_with.append(TransportAuthLost())
    assert (await rig.gw.submit(send("😎Я"))).reason == "auth_lost"
    rig.transport.fail_with.append(TransportRejected("DATA_INVALID"))
    assert (await rig.gw.submit(send("😎Я"))).reason == "rejected:DATA_INVALID"


async def test_pacing_between_sends() -> None:
    paced = Settings(engine=LIVE.engine.model_copy(update={"min_request_interval_s": 0.1}))
    async for r in running_rig(paced):
        await asyncio.gather(r.gw.submit(send("😎Я")), r.gw.submit(send("/full")))
        assert r.transport.sent[1].at - r.transport.sent[0].at >= 0.095


async def test_one_in_flight(rig: Rig) -> None:
    first = asyncio.create_task(rig.gw.submit(send("/job", expect=expect_text("работать"))))
    await until(lambda: len(rig.transport.sent) == 1)
    second = asyncio.create_task(rig.gw.submit(send("😎Я")))
    await until(lambda: rig.gw.queue_size == 1)
    assert [s.payload for s in rig.transport.sent] == ["/job"]
    await rig.deliver(make_msg("Ты отправился работать"))
    await asyncio.gather(first, second)
    assert [s.payload for s in rig.transport.sent] == ["/job", "😎Я"]


async def test_priority_urgent_first() -> None:
    r = Rig()
    planner = asyncio.create_task(r.gw.submit(send("/full")))
    urgent = asyncio.create_task(r.gw.submit(send("😎Я", source=Source.URGENT)))
    await until(lambda: r.gw.queue_size == 2)
    r.start()
    try:
        await asyncio.gather(planner, urgent)
        assert [s.payload for s in r.transport.sent] == ["😎Я", "/full"]
    finally:
        await r.stop()


async def test_lease_allows_others_only_at_safe_point(rig: Rig) -> None:
    lease = await rig.gw.acquire_lease("metro")
    planner = asyncio.create_task(rig.gw.submit(send("/full")))
    manual = asyncio.create_task(rig.gw.submit(send("/inv", source=Source.MANUAL)))
    urgent = asyncio.create_task(rig.gw.submit(send("😎Я", source=Source.URGENT)))
    await until(lambda: rig.gw.queue_size == 3)
    owner = await rig.gw.submit(send("🏢Офис", lease_token=lease.token))
    assert owner.status is ActionStatus.CONFIRMED
    assert [s.payload for s in rig.transport.sent] == ["🏢Офис"]
    await rig.gw.set_safe_point(lease, True)
    await asyncio.gather(urgent, manual)
    assert not planner.done()
    await rig.gw.release_lease(lease)
    await planner
    assert [s.payload for s in rig.transport.sent] == ["🏢Офис", "😎Я", "/inv", "/full"]


async def test_terminal_requests_resolve_behind_lease(rig: Rig) -> None:
    await rig.gw.acquire_lease("metro")
    res = await asyncio.wait_for(rig.gw.submit(send("/job")), 1)
    assert res.reason == "expectation_required"
    res = await asyncio.wait_for(rig.gw.submit(send("😎Я", ttl_s=0.05)), 1)
    assert res.reason == "expired"


async def test_idempotency_sequential_and_concurrent(rig: Rig) -> None:
    a = await rig.gw.submit(send("😎Я", idempotency_key="k"))
    b = await rig.gw.submit(send("😎Я", idempotency_key="k"))
    assert a.action_id == b.action_id and len(rig.transport.sent) == 1
    c, d = await asyncio.gather(
        rig.gw.submit(send("/full", idempotency_key="k2")),
        rig.gw.submit(send("/full", idempotency_key="k2")),
    )
    assert c.action_id == d.action_id and len(rig.transport.sent) == 2


async def test_cancelled_submit_is_withdrawn() -> None:
    r = Rig()
    task = asyncio.create_task(r.gw.submit(send("/full")))
    await until(lambda: r.gw.queue_size == 1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert r.gw.queue_size == 0
    r.start()
    try:
        await r.gw.submit(send("😎Я"))
        assert [s.payload for s in r.transport.sent] == ["😎Я"]
    finally:
        await r.stop()


async def test_shutdown_resolves_queue() -> None:
    r = Rig()
    pending = asyncio.create_task(r.gw.submit(send("/full")))
    await until(lambda: r.gw.queue_size == 1)
    await r.gw.shutdown()
    assert (await pending).reason == "shutdown"
    assert (await r.gw.submit(send("😎Я"))).reason == "shutdown"


async def test_db_unavailable_blocks_actions_but_not_nav(rig: Rig) -> None:
    async def broken(*args: object, **kwargs: object) -> int:
        raise ConnectionError("db down")

    rig.store.create = broken  # type: ignore[method-assign]
    res = await rig.gw.submit(send("/job", expect=expect_text("работать")))
    assert res.reason == "db_unavailable"
    assert (await rig.gw.submit(send("😎Я"))).status is ActionStatus.CONFIRMED
    assert [s.payload for s in rig.transport.sent] == ["😎Я"]


async def test_kill_latch_cancels_queue() -> None:
    r = Rig()
    pending = asyncio.create_task(r.gw.submit(send("/full")))
    await until(lambda: r.gw.queue_size == 1)
    assert await r.gw.kill("test") == 1
    res = await pending
    assert res.status is ActionStatus.SUPPRESSED and res.reason == "kill_switch"


async def test_spending_block(rig: Rig) -> None:
    rig.gw.block_spending("reconcile_required")
    res = await rig.gw.submit(send("/job", expect=expect_text("работать")))
    assert res.status is ActionStatus.REJECTED and res.reason == "blocked:reconcile_required"
    assert (await rig.gw.submit(send("😎Я"))).status is ActionStatus.CONFIRMED
    await rig.gw.allow_spending()
    rig.reply_with("Ты отправился работать")
    res = await rig.gw.submit(send("/job", expect=expect_text("работать")))
    assert res.status is ActionStatus.CONFIRMED


def click(data: str, **kw: object) -> ActionRequest:
    return ActionRequest(
        kind=ActionKind.CLICK,
        chat_id=GAME,
        message_id=5,
        data=data,
        **kw,  # type: ignore[arg-type]
    )


async def test_spending_block_lets_only_wake_click_through(rig: Rig) -> None:
    buttons = (Button("Проснуться", 0, 0, "rob_awake_7"), Button("Бой", 0, 1, "gorbushka_fight"))
    rig.latest[(GAME, 5)] = make_msg("Опа, тебя начал грабить", msg_id=5, buttons=buttons)
    rig.gw.block_spending("reconcile_required")
    fight = await rig.gw.submit(click("gorbushka_fight", expect=expect_text("продаван")))
    assert (fight.status, fight.reason) == (ActionStatus.REJECTED, "blocked:reconcile_required")
    rig.reply_with("Отлично, ты проснулся")
    wake = await rig.gw.submit(click("rob_awake_7", expect=expect_text("проснулся")))
    assert wake.status is ActionStatus.CONFIRMED
    assert rig.gw.spending_blocked == "reconcile_required"


async def test_safe_point_lasts_until_owner_step_is_picked(rig: Rig) -> None:
    lease = await rig.gw.acquire_lease("scenario")
    await rig.gw.set_safe_point(lease, True)
    owner = asyncio.create_task(
        rig.gw.submit(send("/job", expect=expect_text("работать"), lease_token=lease.token))
    )
    await until(lambda: len(rig.transport.sent) == 1)
    # Шаг аренды выбран — безопасная точка закрыта: срочное ждёт следующей.
    assert lease.safe is False
    urgent = asyncio.create_task(rig.gw.submit(send("😎Я", source=Source.URGENT)))
    await until(lambda: rig.gw.queue_size == 1)
    await rig.deliver(make_msg("Ты отправился работать"))
    await owner
    await asyncio.sleep(0.02)
    assert not urgent.done()
    await rig.gw.set_safe_point(lease, True)
    assert (await urgent).status is ActionStatus.CONFIRMED
    await rig.gw.release_lease(lease)


async def test_cancelled_keyed_submit_is_not_withdrawn() -> None:
    r = Rig()
    task = asyncio.create_task(r.gw.submit(send("/full", idempotency_key="kc")))
    await until(lambda: r.gw.queue_size == 1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert r.gw.queue_size == 1
    r.start()
    try:
        res = await r.gw.submit(send("/full", idempotency_key="kc"))
        assert res.status is ActionStatus.CONFIRMED
        assert [s.payload for s in r.transport.sent] == ["/full"]
    finally:
        await r.stop()


async def test_key_lookup_failure_does_not_block_nav(rig: Rig) -> None:
    async def broken(*args: object, **kwargs: object) -> None:
        raise ConnectionError("db down")

    rig.store.get_by_key = broken  # type: ignore[method-assign]
    res = await rig.gw.submit(send("😎Я", idempotency_key="k-down"))
    assert res.status is ActionStatus.CONFIRMED
    assert len(rig.transport.sent) == 1
