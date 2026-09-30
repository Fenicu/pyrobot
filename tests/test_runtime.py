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
from app.db.accounts import AccountInfo, AccountRepo
from app.db.actions import DbActionStore
from app.db.base import Database
from app.db.journal import DbJournal
from app.db.models import Account, DecisionRow, MessageRow, ScenarioRunRow
from app.db.planner import DbPlannerStore
from app.engine.commands import CommandClass
from app.engine.fence import Fence
from app.engine.gateway.gateway import RECONCILE_REASON
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus, Source
from app.engine.host import account as account_engine
from app.engine.host.lease import LeaseManager
from app.engine.planner.types import Act
from app.engine.settings import Settings
from app.engine.transport.fake import Sent
from app.main import Runtime, create_application
from tests.api.sse import read_sse
from tests.conftest import TEST_DB_URL
from tests.engine.helpers import GAME, make_msg, now, until
from tests.fixtures import game_msg

pytestmark = pytest.mark.db
A1 = "/api/v1/accounts/1"


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


async def _login(client: AsyncClient) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/login", json={"login": "admin", "password": "correct horse battery"}
    )
    return {"X-CSRF-Token": r.json()["csrf_token"]}


async def _login_tg(client: AsyncClient) -> dict[str, str]:
    h = await _login(client)
    st = await client.post("/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+888"})
    code = await client.post(
        "/api/v1/accounts/1/tg/login/code",
        headers=h,
        json={"attempt_id": st.json()["attempt_id"], "code": "12345"},
    )
    assert code.json()["state"] == "online"
    return h


async def _account(repo: AccountRepo) -> AccountInfo:
    account = await repo.get(1)
    assert account is not None
    return account


def _engine(runtime: Runtime) -> Any:
    """Движок аккаунта процесса (запущен)."""
    assert runtime.account is not None
    return runtime.account


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
            st = await client.post(
                "/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+888"}
            )
            code = await client.post(
                "/api/v1/accounts/1/tg/login/code",
                headers=h,
                json={"attempt_id": st.json()["attempt_id"], "code": "12345"},
            )
            assert code.json()["state"] == "online"
            assert (await client.get("/readyz")).status_code == 200
            status = (await client.get("/api/v1/accounts/1/engine/status")).json()
            assert status["mode"] == "dry_run" and status["lock_ok"] is True
            # Реакция на ограбление и пересылка в чат команды — свои задачи под супервизором
            # движка аккаунта, продление аренды и ретеншн — под супервизором процесса.
            engine = _engine(app.state.runtime)
            assert {"reactions", "team-forward"} <= set(engine.supervisor._tasks)
            assert {"lease", "retention", "engine"} <= set(app.state.runtime.supervisor._tasks)


async def test_first_login_binds_telegram_account(clean_db: Database) -> None:
    repo = AccountRepo(clean_db)
    assert (await _account(repo)).tg_user_id is None
    app = create_application(_cfg())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            await _login_tg(client)
            assert (await client.get("/api/v1/accounts/1/tg/status")).json()[
                "bound_user_id"
            ] == 267519921
            # Привязка — в `accounts`, а не в настройках: версии настроек она не порождает.
            settings = (await client.get("/api/v1/accounts/1/settings")).json()
            assert "telegram" not in settings["values"]
            assert (await client.get("/api/v1/accounts/1/settings/history")).json()["items"] == []
    assert (await _account(repo)).tg_user_id == 267519921


async def test_start_takes_binding_from_account(clean_db: Database) -> None:
    repo = AccountRepo(clean_db)
    await repo.bind_telegram(1, 42)
    app = create_application(_cfg())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                "/api/v1/auth/login", json={"login": "admin", "password": "correct horse battery"}
            )
            h = {"X-CSRF-Token": r.json()["csrf_token"]}
            assert (await client.get("/api/v1/accounts/1/tg/status")).json()["bound_user_id"] == 42
            st = await client.post(
                "/api/v1/accounts/1/tg/login/start", headers=h, json={"phone": "+888"}
            )
            code = await client.post(
                "/api/v1/accounts/1/tg/login/code",
                headers=h,
                json={"attempt_id": st.json()["attempt_id"], "code": "12345"},
            )
            # Вошёл не привязанный пользователь: сервис от него отказывается.
            assert code.json()["state"] == "error" and code.json()["error"] == "unexpected_user"
    assert (await _account(repo)).tg_user_id == 42


async def test_second_runtime_does_not_start_engine(clean_db: Database) -> None:
    first = create_application(_cfg())
    second = create_application(_cfg())
    async with first.router.lifespan_context(first), second.router.lifespan_context(second):
        async with AsyncClient(transport=ASGITransport(app=second), base_url="http://t") as client:
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.get("/readyz")).status_code == 503
        assert first.state.runtime.account is not None
        assert second.state.runtime.account is None
        assert await _notified(second.state.runtime, "second_instance") == 1


async def test_second_instance_starts_engine_after_lease_released(clean_db: Database) -> None:
    # Аккаунт держит другой хост: процесс работает без движка и повторяет захват.
    other = LeaseManager(clean_db, "other-host")
    await other.open()
    try:
        fence = await other.acquire(1)
        assert isinstance(fence, Fence)
        app = create_application(_cfg())
        runtime = app.state.runtime
        runtime.leases = LeaseManager(runtime.db, "this-host", busy_retry_s=0.02)
        async with app.router.lifespan_context(app):
            assert runtime.account is None and runtime.get(1) is None
            assert runtime.host_reason(1) == "locked_elsewhere"
            assert await runtime.wait_registered(1, 0.05) is None
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                assert (await c.get("/readyz")).status_code == 503
                await _login(c)
                status = (await c.get(f"{A1}/engine/status")).json()
                assert (status["running"], status["host_reason"]) == (False, "locked_elsewhere")
            await asyncio.sleep(0.1)
            # Повторы захвата не повторяют уведомление.
            assert await _notified(runtime, "second_instance") == 1
            registered = asyncio.create_task(runtime.wait_registered(1, 5.0))
            await other.release(fence)
            engine = await registered
            assert engine is not None and engine is runtime.get(1) is runtime.account
            assert runtime.host_reason(1) is None and runtime.get(2) is None
            assert engine.fence.epoch == fence.epoch + 1
    finally:
        await other.close()


async def _notified(runtime: Any, code: str, timeout: float = 5.0) -> int:  # noqa: ASYNC109
    # Уведомление пишется в БД после остановки движка: ждём первую запись, а не паузу.
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        count = sum(row.code == code for row in await runtime.notifier.recent())
        if count or asyncio.get_running_loop().time() > deadline:
            return count
        await asyncio.sleep(0.01)


async def test_lease_lost_aborts_engine_and_takes_lease_again(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            await _login_tg(client)
            assert (await client.get("/readyz")).status_code == 200
            first = _engine(runtime)
            tasks = list(first.supervisor._tasks.values())
            first.fence.revoke()
            # Останавливаемый движок API уже не видит.
            assert runtime.get(1) is None
            await until(lambda: runtime.account not in (None, first), 5.0)
            # Аварийная остановка: задачи прежнего движка отменены; новый — на новой эпохе.
            assert all(t.done() for t in tasks) and not first.supervisor._tasks
            assert _engine(runtime).fence.epoch == first.fence.epoch + 1
            assert runtime.get(1) is _engine(runtime)
            assert await _notified(runtime, "lock_lost") == 1
            await asyncio.sleep(0.1)
            assert await _notified(runtime, "lock_lost") == 1
            # Вход фейкового транспорта живёт в движке: новый движок — без входа.
            assert (await client.get("/readyz")).status_code == 503


async def test_engine_abort_ends_its_event_streams(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            await _login(client)
            cookie = {"cookie": f"pyrobot_session={client.cookies['pyrobot_session']}"}
            first = _engine(runtime)

            async def lose() -> None:
                await until(lambda: first.stream.subscribers == 1, 5.0)
                first.fence.revoke()

            task = asyncio.create_task(lose())
            # Поток прежнего движка кончается с его аварийной остановкой, а не молчит.
            status, events = await read_sse(
                app, f"{A1}/events", headers=cookie, count=100, timeout=5.0
            )
            await task
            assert status == 200 and [e.event for e in events] == ["reset"]
            await until(lambda: runtime.account not in (None, first), 5.0)
            assert _engine(runtime).stream is not first.stream


async def test_crash_loop_stops_engine_until_restart(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    async with app.router.lifespan_context(app):
        engine = _engine(runtime)
        # Так супервизор движка сообщает о серии сбоев задачи.
        await engine.supervisor._on_crash_loop("gateway")
        await until(lambda: runtime.account is None, 5.0)
        assert not engine.supervisor._tasks
        assert await _notified(runtime, "account_crash_loop") == 1
        await asyncio.sleep(0.1)
        assert runtime.account is None and runtime.get(1) is None
        # Аренда освобождена штатно: другой хост захватит аккаунт без ожидания срока.
        async with clean_db.sessions() as s:
            holder = await s.scalar(select(Account.lease_holder).where(Account.id == 1))
        assert holder is None
        assert "lock_lost" not in {r.code for r in await runtime.notifier.recent()}


async def test_unkill_after_lock_lost_is_conflict(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            h = await _login_tg(client)
            # Ограда отозвана, аварийная остановка ещё не прошла: фасад на месте.
            fence = _engine(runtime).fence
            fence.on_lost = None
            fence.revoke()
            r = await client.post("/api/v1/accounts/1/engine/unkill", headers=h)
            assert r.status_code == 409 and r.json() == {"detail": "lock_lost"}
            status = (await client.get("/api/v1/accounts/1/engine/status")).json()
            assert status["lock_ok"] is False
            assert (await client.get("/readyz")).status_code == 503


async def test_gateway_rejects_when_tg_offline(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    nav = ActionRequest(kind=ActionKind.SEND, chat_id=GAME, text="😎Я")
    async with app.router.lifespan_context(app):
        engine = _engine(runtime)
        res = await engine.gateway.submit(nav)
        assert res.status is ActionStatus.REJECTED and res.reason == "tg_offline"
        assert engine.transport.sent == []
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            await _login_tg(client)
        ok = await engine.gateway.submit(nav)
        assert ok.status is ActionStatus.CONFIRMED
        assert [s.payload for s in engine.transport.sent] == ["😎Я"]


async def test_stop_drains_pipeline_into_journal(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    async with app.router.lifespan_context(app):
        for i in range(50):
            await _engine(runtime).pipeline.submit(make_msg(f"m{i}", msg_id=i + 1))
    async with clean_db.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(MessageRow)) == 50


async def test_reconciler_lifts_block_from_restart_obligation(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Незавершённое действие с прошлого запуска (класс не nav) — обязательство сверки,
    # которое движок должен снять сам, без ручного POST /engine/reconciled.
    stuck = ActionRequest(
        kind=ActionKind.SEND, chat_id=GAME, text="/harvest", source=Source.PLANNER
    )
    await DbActionStore(clean_db, 1).create(stuck, CommandClass.ACTION, ActionStatus.SENT)

    monkeypatch.setattr(account_engine, "RECONCILE_POLL_S", 0.05)
    app = create_application(_cfg())
    runtime = app.state.runtime
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
        await _engine(runtime).pipeline.submit(msg)

    async with app.router.lifespan_context(app):
        engine = _engine(runtime)
        assert engine.gateway.spending_blocked == RECONCILE_REASON
        engine.transport.responder = respond
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            await _login_tg(client)
            await until(lambda: engine.gateway.spending_blocked is None)
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


async def test_planner_refreshes_state_in_dry_run(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(account_engine, "PLANNER_POLL_S", 0.05)
    app = create_application(_cfg().model_copy(update={"planner": True}))
    runtime = app.state.runtime
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
        await _engine(runtime).pipeline.submit(msg)

    async with app.router.lifespan_context(app):
        engine = _engine(runtime)
        # Рефреш проверяется на механиках фазы 3: окна обязательств, сна и полуночи заданий
        # зависят от часов.
        await engine.settings.update(_without_windows, changed_by="test")
        engine.transport.responder = respond
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            h = await _login_tg(client)
            # Второй запрос уходит после паузы шлюза между запросами (1.6 с).
            await until(lambda: "/inv" in [s.payload for s in engine.transport.sent], 5.0)
            assert [s.payload for s in engine.transport.sent][:2] == ["😎Я", "/inv"]
            await client.post("/api/v1/accounts/1/engine/pause", headers=h)
            status = (await client.get("/api/v1/accounts/1/engine/status")).json()
            assert status["paused"] is True
    async with clean_db.sessions() as s:
        decided = await s.scalar(select(func.count()).select_from(DecisionRow))
        runs = (await s.scalars(select(ScenarioRunRow.scenario))).all()
    assert decided is not None and decided >= 2
    assert list(runs[:1]) == ["refresh"]


async def test_forward_left_by_previous_process_notified_not_retried(clean_db: Database) -> None:
    # Процесс упал посреди пересылки в чат команды: ушла ли копия — неизвестно. При старте —
    # отдельное уведомление, повтора нет (ключ израсходован), сверки нет.
    store = DbActionStore(clean_db, 1)
    team = -1001149209877
    req = ActionRequest(
        kind=ActionKind.FORWARD,
        chat_id=team,
        from_chat_id=GAME,
        message_id=3625831,
        idempotency_key=f"forward:{GAME}:3625831",
        expect_content="h",
    )
    forwarded = await store.create(req, CommandClass.FORWARD, ActionStatus.SENT)
    await store.create(
        replace(req, message_id=3625832, idempotency_key=f"forward:{GAME}:3625832"),
        CommandClass.FORWARD,
        ActionStatus.CONFIRMED,
    )
    app = create_application(_cfg())
    runtime = app.state.runtime
    async with app.router.lifespan_context(app):
        rows = [r for r in await runtime.notifier.recent() if r.code == "team_forward_unknown"]
        assert len(rows) == 1 and "3625831" in rows[0].text
        assert not any(
            r.code == "actions_outcome_unknown" for r in await runtime.notifier.recent()
        )
        assert _engine(runtime).gateway.spending_blocked is None
        assert _engine(runtime).transport.sent == []
    stored = await store.get_by_key(f"forward:{GAME}:3625831")
    assert stored is not None and stored.id == forwarded
    assert (stored.status, stored.reason) == (ActionStatus.OUTCOME_UNKNOWN, "restart")
    # Повторный старт — без второго уведомления.
    again = create_application(_cfg())
    async with again.router.lifespan_context(again):
        codes = [r.code for r in await again.state.runtime.notifier.recent()]
    assert codes.count("team_forward_unknown") == 1


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
        engine = _engine(runtime)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            h = await _login_tg(client)
            cookie = {"cookie": f"pyrobot_session={client.cookies['pyrobot_session']}"}
            status, events = await read_sse(
                app, "/api/v1/accounts/1/events", headers=cookie, count=1
            )
            assert status == 200 and events[0].event == "reset"
            await client.post("/api/v1/accounts/1/engine/pause", headers=h)
            await client.post(
                "/api/v1/accounts/1/commands/send",
                headers=h,
                json={"text": "😎Я", "idempotency_key": "s1"},
            )
            await engine.pipeline.submit(make_msg("что-то новое", msg_id=77))
            await until(lambda: "message" in {e.type for e in engine.stream.history()})
    kinds = [e.type for e in engine.stream.history()]
    assert {"settings", "notification", "action", "message"} <= set(kinds)
    # Первая версия настроек — привязка аккаунта при входе, пауза — последняя.
    paused = [e for e in engine.stream.history() if e.type == "settings"][-1]
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
