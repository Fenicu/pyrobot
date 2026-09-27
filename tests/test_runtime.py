import asyncio
import itertools
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select

from app.config import AppConfig
from app.db.actions import DbActionStore
from app.db.base import Database
from app.db.journal import DbJournal
from app.db.models import DecisionRow, MessageRow, ScenarioRunRow
from app.db.planner import DbPlannerStore
from app.engine.commands import CommandClass
from app.engine.gateway.gateway import RECONCILE_REASON
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus, Source
from app.engine.planner.types import Act
from app.engine.settings import Settings
from app.engine.supervisor import Supervisor
from app.engine.transport.fake import Sent
from app.main import create_application
from tests.api.sse import read_sse
from tests.conftest import TEST_DB_URL
from tests.engine.helpers import GAME, make_msg, now, until
from tests.fixtures import game_msg

pytestmark = pytest.mark.db


def _cfg() -> AppConfig:
    return AppConfig(
        _env_file=None,
        database_url=TEST_DB_URL,
        transport="fake",
        cookie_secure=False,
        admin_login="admin",
        admin_password=SecretStr("correct horse battery"),
        planner=False,
    )


async def _login_tg(client: AsyncClient) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/login", json={"login": "admin", "password": "correct horse battery"}
    )
    h = {"X-CSRF-Token": r.json()["csrf_token"]}
    st = await client.post("/api/v1/tg/login/start", headers=h, json={"phone": "+888"})
    code = await client.post(
        "/api/v1/tg/login/code",
        headers=h,
        json={"attempt_id": st.json()["attempt_id"], "code": "12345"},
    )
    assert code.json()["state"] == "online"
    return h


async def test_fake_runtime_login_to_ready(clean_db: Database) -> None:
    app = create_application(_cfg())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.get("/readyz")).status_code == 503
            r = await client.post(
                "/api/v1/auth/login", json={"login": "admin", "password": "correct horse battery"}
            )
            h = {"X-CSRF-Token": r.json()["csrf_token"]}
            st = await client.post("/api/v1/tg/login/start", headers=h, json={"phone": "+888"})
            code = await client.post(
                "/api/v1/tg/login/code",
                headers=h,
                json={"attempt_id": st.json()["attempt_id"], "code": "12345"},
            )
            assert code.json()["state"] == "online"
            assert (await client.get("/readyz")).status_code == 200
            status = (await client.get("/api/v1/engine/status")).json()
            assert status["mode"] == "dry_run" and status["lock_ok"] is True
            # Реакция на ограбление — своя задача под супервизором.
            assert "reactions" in app.state.runtime.supervisor._tasks


async def test_second_runtime_does_not_start_engine(clean_db: Database) -> None:
    first = create_application(_cfg())
    second = create_application(_cfg())
    async with first.router.lifespan_context(first), second.router.lifespan_context(second):
        async with AsyncClient(transport=ASGITransport(app=second), base_url="http://t") as client:
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.get("/readyz")).status_code == 503


async def _notified(runtime: Any, code: str, timeout: float = 5.0) -> int:  # noqa: ASYNC109
    # Уведомление пишется в БД после kill: ждём первую запись, а не фиксированную паузу.
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        count = sum(row.code == code for row in await runtime.notifier.recent())
        if count or asyncio.get_running_loop().time() > deadline:
            return count
        await asyncio.sleep(0.01)


async def test_lock_lost_notifies_once_and_parks_watch(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    # ускоряем lock-watch и супервизор, чтобы повторное срабатывание (если баг есть)
    # проявилось за доли секунды, а не за реальные LOCK_CHECK_S=10с/backoff=60с
    runtime.lock_check_s = 0.01
    runtime.supervisor = Supervisor(runtime.notifier, base_s=0.01, max_s=0.02)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                "/api/v1/auth/login", json={"login": "admin", "password": "correct horse battery"}
            )
            h = {"X-CSRF-Token": r.json()["csrf_token"]}
            st = await client.post("/api/v1/tg/login/start", headers=h, json={"phone": "+888"})
            code = await client.post(
                "/api/v1/tg/login/code",
                headers=h,
                json={"attempt_id": st.json()["attempt_id"], "code": "12345"},
            )
            assert code.json()["state"] == "online"
            assert (await client.get("/readyz")).status_code == 200
            assert runtime.lock._conn is not None
            await runtime.lock._conn.invalidate()
            await until(lambda: runtime.gateway.kill_reason == "lock_lost")
            assert (await client.get("/readyz")).status_code == 503
            assert await _notified(runtime, "lock_lost") == 1
            await asyncio.sleep(0.2)
            assert await _notified(runtime, "lock_lost") == 1


async def test_unkill_after_lock_lost_is_conflict(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    runtime.lock_check_s = 0.01
    runtime.supervisor = Supervisor(runtime.notifier, base_s=0.01, max_s=0.02)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            h = await _login_tg(client)
            await runtime.lock._conn.invalidate()
            await until(lambda: runtime.gateway.kill_reason == "lock_lost")
            r = await client.post("/api/v1/engine/unkill", headers=h)
            assert r.status_code == 409 and r.json() == {"detail": "lock_lost"}
            assert runtime.gateway.kill_reason == "lock_lost"
            status = (await client.get("/api/v1/engine/status")).json()
            assert status["killed"] is True


async def test_gateway_rejects_when_tg_offline(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    nav = ActionRequest(kind=ActionKind.SEND, chat_id=GAME, text="😎Я")
    async with app.router.lifespan_context(app):
        res = await runtime.gateway.submit(nav)
        assert res.status is ActionStatus.REJECTED and res.reason == "tg_offline"
        assert runtime.transport.sent == []
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            await _login_tg(client)
        ok = await runtime.gateway.submit(nav)
        assert ok.status is ActionStatus.CONFIRMED
        assert [s.payload for s in runtime.transport.sent] == ["😎Я"]


async def test_stop_drains_pipeline_into_journal(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    async with app.router.lifespan_context(app):
        for i in range(50):
            await runtime.pipeline.submit(make_msg(f"m{i}", msg_id=i + 1))
    async with clean_db.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(MessageRow)) == 50


async def test_reconciler_lifts_block_from_restart_obligation(clean_db: Database) -> None:
    # Незавершённое действие с прошлого запуска (класс не nav) — обязательство сверки,
    # которое движок должен снять сам, без ручного POST /engine/reconciled.
    stuck = ActionRequest(
        kind=ActionKind.SEND, chat_id=GAME, text="/harvest", source=Source.PLANNER
    )
    await DbActionStore(clean_db, 1).create(stuck, CommandClass.ACTION, ActionStatus.SENT)

    app = create_application(_cfg())
    runtime = app.state.runtime
    runtime.reconcile_poll_s = 0.05
    ids = itertools.count(5_000_000)

    async def respond(rec: Sent) -> None:
        if rec.payload != "😎Я":
            return
        moment = now()
        msg = replace(
            game_msg("profile", 3624478),
            msg_id=next(ids),
            date=moment,
            created_at=moment,
            received_at=moment,
        )
        assert runtime.pipeline is not None
        await runtime.pipeline.submit(msg)

    async with app.router.lifespan_context(app):
        assert runtime.gateway is not None
        assert runtime.gateway.spending_blocked == RECONCILE_REASON
        assert runtime.transport is not None
        runtime.transport.responder = respond
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            await _login_tg(client)
            await until(lambda: runtime.gateway.spending_blocked is None)
            assert await DbActionStore(clean_db, 1).unreconciled() == []
            assert (await client.get("/readyz")).status_code == 200


def _without_windows(s: Settings) -> Settings:
    off = dict.fromkeys(
        (
            "stocks_dump",
            "factory",
            "bulls",
            "tangerine",
            "smoothie",
            "sleep",
            "metro",
            "daily_tasks",
            "lottery",
        ),
        False,
    )
    return s.model_copy(update={"features": s.features.model_copy(update=off)})


async def test_planner_refreshes_state_in_dry_run(clean_db: Database) -> None:
    app = create_application(_cfg().model_copy(update={"planner": True}))
    runtime = app.state.runtime
    runtime.planner_poll_s = 0.05
    ids = itertools.count(6_000_000)

    async def respond(rec: Sent) -> None:
        if rec.payload != "😎Я":
            return
        moment = now()
        msg = replace(
            game_msg("profile", 3624478),
            msg_id=next(ids),
            date=moment,
            created_at=moment,
            received_at=moment,
        )
        assert runtime.pipeline is not None
        await runtime.pipeline.submit(msg)

    async with app.router.lifespan_context(app):
        # Рефреш проверяется на механиках фазы 3: окна обязательств, сна и полуночи заданий
        # зависят от часов.
        await runtime.settings.update(_without_windows, changed_by="test")
        assert runtime.transport is not None
        runtime.transport.responder = respond
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            h = await _login_tg(client)
            # Второй запрос уходит после паузы шлюза между запросами (1.6 с).
            await until(lambda: "/inv" in [s.payload for s in runtime.transport.sent], 5.0)
            assert [s.payload for s in runtime.transport.sent][:2] == ["😎Я", "/inv"]
            await client.post("/api/v1/engine/pause", headers=h)
            status = (await client.get("/api/v1/engine/status")).json()
            assert status["paused"] is True
    async with clean_db.sessions() as s:
        decided = await s.scalar(select(func.count()).select_from(DecisionRow))
        runs = (await s.scalars(select(ScenarioRunRow.scenario))).all()
    assert decided is not None and decided >= 2
    assert list(runs[:1]) == ["refresh"]


async def test_start_interrupts_runs_left_by_previous_process(clean_db: Database) -> None:
    store = DbPlannerStore(clean_db, 1)
    moment = now()
    left = await store.run_started(
        await store.record(moment, Act("book", {}, "book_ready")), "book", {}, moment
    )
    app = create_application(_cfg())
    async with app.router.lifespan_context(app):
        pass
    async with clean_db.sessions() as s:
        run = await s.get(ScenarioRunRow, left)
    assert run is not None and (run.status, run.reason) == ("interrupted", "restart")
    assert run.finished_at is not None and run.finished_at >= moment


async def test_runtime_streams_engine_events(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            h = await _login_tg(client)
            cookie = {"cookie": f"pyrobot_session={client.cookies['pyrobot_session']}"}
            status, events = await read_sse(app, "/api/v1/events", headers=cookie, count=1)
            assert status == 200 and events[0].event == "reset"
            await client.post("/api/v1/engine/pause", headers=h)
            await client.post(
                "/api/v1/commands/send", headers=h, json={"text": "😎Я", "idempotency_key": "s1"}
            )
            await runtime.pipeline.submit(make_msg("что-то новое", msg_id=77))
            await until(lambda: "message" in {e.type for e in runtime.stream.history()})
    kinds = [e.type for e in runtime.stream.history()]
    assert {"settings", "notification", "action", "message"} <= set(kinds)
    paused = next(e for e in runtime.stream.history() if e.type == "settings")
    assert paused.data["paused"] is True


async def test_retention_task_purges_old_journal(clean_db: Database) -> None:
    old = now() - timedelta(days=91)
    await DbJournal(clean_db, 1).append(make_msg("old", msg_id=1, received_at=old), [], None, 0)
    app = create_application(_cfg())
    runtime = app.state.runtime
    runtime.retention_first_s = 0.0

    async def journal_size() -> int:
        async with clean_db.sessions() as s:
            return int(await s.scalar(select(func.count()).select_from(MessageRow)) or 0)

    async with app.router.lifespan_context(app):
        for _ in range(200):
            if await journal_size() == 0:
                break
            await asyncio.sleep(0.01)
        assert await journal_size() == 0


async def test_retention_failure_notifies_once(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    runtime.retention_first_s = 0.0
    runtime.retention_s = 0.01
    calls = [0]

    async def broken(*args: object) -> dict[str, int]:
        calls[0] += 1
        raise ConnectionError("db down")

    runtime.retention.purge = broken
    async with app.router.lifespan_context(app):
        await until(lambda: calls[0] >= 3)
        assert await _notified(runtime, "retention_failed") == 1
        assert "retention" not in runtime.supervisor._backoff
