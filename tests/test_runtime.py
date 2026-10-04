import asyncio
import base64
import contextlib
import itertools
import secrets
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select

from app.config import AppConfig
from app.db.accounts import AccountInfo, AccountRepo
from app.db.actions import DbActionStore
from app.db.base import Database
from app.db.crypto import SecretBox, SecretKeyError, ensure_key
from app.db.journal import DbJournal
from app.db.models import DecisionRow, MessageRow, ScenarioRunRow, TgPeer, TgSession
from app.db.notifications import DbNotifier
from app.db.planner import DbPlannerStore
from app.db.retention import DbRetention
from app.engine.commands import CommandClass
from app.engine.fence import Fence
from app.engine.gateway.gateway import RECONCILE_REASON
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus, Source
from app.engine.host import account as account_engine
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


def _cfg(**changes: Any) -> AppConfig:
    values: dict[str, Any] = {
        "database_url": TEST_DB_URL,
        "transport": "fake",
        "cookie_secure": False,
        "admin_login": "admin",
        "admin_password": SecretStr("correct horse battery"),
        "planner": False,
        **changes,
    }
    return AppConfig(_env_file=None, **values)


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


@contextlib.asynccontextmanager
async def _started(app: FastAPI) -> AsyncIterator[None]:
    """Процесс запущен и движок аккаунта 1 поднят: плавный старт движков идёт в фоне."""
    async with app.router.lifespan_context(app):
        assert await app.state.runtime.host.wait_registered(1, 5.0) is not None
        yield


def _engine(runtime: Runtime, account_id: int = 1) -> Any:
    """Зарегистрированный движок аккаунта."""
    engine = runtime.host.get(account_id)
    assert engine is not None
    return engine


async def _notified(db: Database, code: str, timeout: float = 5.0) -> int:  # noqa: ASYNC109
    # Уведомление пишется в БД после остановки движка: ждём первую запись, а не паузу.
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        count = sum(row.code == code for row in await DbNotifier(db, 1).recent())
        if count or asyncio.get_running_loop().time() > deadline:
            return count
        await asyncio.sleep(0.01)


async def test_fake_runtime_ready_without_telegram(clean_db: Database) -> None:
    app = create_application(_cfg())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            assert (await client.get("/healthz")).status_code == 200
            # Готовность процесса — база и соединение блокировок; ни движки (их плавный старт
            # идёт в фоне), ни Telegram аккаунта не в счёт.
            assert (await client.get("/readyz")).status_code == 200
            assert await app.state.runtime.host.wait_registered(1, 5.0) is not None
            await _login_tg(client)
            assert (await client.get("/readyz")).status_code == 200
            status = (await client.get("/api/v1/accounts/1/engine/status")).json()
            assert status["mode"] == "dry_run" and status["lease_ok"] is True
            assert "lock_ok" not in status and "loop_lag_ms" not in status
            # Реакция на ограбление и пересылка в чат команды — свои задачи под супервизором
            # движка аккаунта; сверка хоста, продление аренды и ретеншн — под супервизором
            # процесса.
            runtime = app.state.runtime
            engine = _engine(runtime)
            assert {"reactions", "team-forward"} <= set(engine.supervisor._tasks)
            assert {"reconcile", "lease", "lag", "session-purge", "retention"} <= set(
                runtime.supervisor._tasks
            )
            assert runtime.host.status().tasks_ok


async def test_account_id_in_env_is_ignored_with_warning(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("PYROBOT_ACCOUNT_ID", "7")
    app = create_application(_cfg())
    async with _started(app):
        assert app.state.runtime.host.status().engines == [1]
    assert "PYROBOT_ACCOUNT_ID больше не читается" in caplog.text


async def test_stop_order_engines_release_renewal_close(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Остановка: движки, освобождение их аренд, продление, соединение блокировок. Отмена
    # продления посреди запроса обрывает соединение со всеми блокировками — поэтому после
    # освобождения.
    app = create_application(_cfg())
    runtime = app.state.runtime
    leases = runtime.leases
    run, release, close = leases.run, leases.release, leases.close
    order: list[str] = []

    async def renewing() -> None:
        try:
            await run()
        finally:
            order.append("renewal stopped")

    async def releasing(fence: Fence) -> None:
        order.append(f"release {fence.account_id}")
        await release(fence)

    async def closing() -> None:
        order.append("close")
        await close()

    monkeypatch.setattr(leases, "run", renewing)
    monkeypatch.setattr(leases, "release", releasing)
    monkeypatch.setattr(leases, "close", closing)
    async with _started(app):
        engine = _engine(runtime)
        stop = engine.stop

        async def stopping() -> None:
            order.append("engine stop")
            await stop()

        engine.stop = stopping
    assert order == ["engine stop", "release 1", "renewal stopped", "close"]


async def test_first_login_binds_telegram_account(clean_db: Database) -> None:
    repo = AccountRepo(clean_db)
    assert (await _account(repo)).tg_user_id is None
    app = create_application(_cfg())
    async with _started(app):
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
    async with _started(app):
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
    async with _started(first), second.router.lifespan_context(second):
        host = second.state.runtime.host
        await until(lambda: host.host_reason(1) is not None, 5.0)
        assert first.state.runtime.host.get(1) is not None
        assert host.get(1) is None and host.host_reason(1) == "locked_elsewhere"
        async with AsyncClient(transport=ASGITransport(app=second), base_url="http://t") as client:
            # Процесс готов и без движков: аккаунт работает на другом хосте.
            assert (await client.get("/readyz")).status_code == 200
            await _login(client)
            status = (await client.get(f"{A1}/engine/status")).json()
            assert (status["running"], status["host_reason"]) == (False, "locked_elsewhere")


async def test_engine_abort_ends_its_event_streams(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    async with _started(app):
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
            await until(lambda: runtime.host.get(1) not in (None, first), 5.0)
            assert _engine(runtime).stream is not first.stream


async def test_unkill_after_lock_lost_is_conflict(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    async with _started(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            h = await _login_tg(client)
            # Ограда отозвана, аварийная остановка ещё не прошла: фасад на месте.
            fence = _engine(runtime).fence
            fence.on_lost = None
            fence.revoke()
            r = await client.post("/api/v1/accounts/1/engine/unkill", headers=h)
            assert r.status_code == 409 and r.json() == {"detail": "lock_lost"}
            status = (await client.get("/api/v1/accounts/1/engine/status")).json()
            assert status["lease_ok"] is False
            # Аренда одного аккаунта — его статус, а не готовность процесса.
            assert (await client.get("/readyz")).status_code == 200


async def test_gateway_rejects_when_tg_offline(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    nav = ActionRequest(kind=ActionKind.SEND, chat_id=GAME, text="😎Я")
    async with _started(app):
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
    async with _started(app):
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
        if rec.payload != "/compact":
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

    async with _started(app):
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
        if rec.payload != "/compact":
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

    async with _started(app):
        engine = _engine(runtime)
        # Рефреш проверяется на механиках фазы 3: окна обязательств, сна и полуночи заданий
        # зависят от часов.
        await engine.settings.update(_without_windows, changed_by="test")
        engine.transport.responder = respond
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            h = await _login_tg(client)
            # Второй запрос уходит после паузы шлюза между запросами (1.6 с).
            await until(lambda: "/inv" in [s.payload for s in engine.transport.sent], 5.0)
            assert [s.payload for s in engine.transport.sent][:2] == ["/compact", "/inv"]
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
    notifier = DbNotifier(clean_db, 1)
    async with _started(app):
        rows = [r for r in await notifier.recent() if r.code == "team_forward_unknown"]
        assert len(rows) == 1 and "3625831" in rows[0].text
        assert not any(r.code == "actions_outcome_unknown" for r in await notifier.recent())
        assert _engine(runtime).gateway.spending_blocked is None
        assert _engine(runtime).transport.sent == []
    stored = await store.get_by_key(f"forward:{GAME}:3625831")
    assert stored is not None and stored.id == forwarded
    assert (stored.status, stored.reason) == (ActionStatus.OUTCOME_UNKNOWN, "restart")
    # Повторный старт — без второго уведомления.
    again = create_application(_cfg())
    async with _started(again):
        codes = [r.code for r in await notifier.recent()]
    assert codes.count("team_forward_unknown") == 1


async def test_start_interrupts_runs_left_by_previous_process(clean_db: Database) -> None:
    store = DbPlannerStore(clean_db, 1)
    moment = now()
    left = await store.run_started(
        await store.record(moment, Act("book", {}, "book_ready")), "book", {}, moment
    )
    app = create_application(_cfg())
    async with _started(app):
        pass
    async with clean_db.sessions() as s:
        run = await s.get(ScenarioRunRow, left)
    assert run is not None and (run.status, run.reason) == ("interrupted", "restart")
    assert run.finished_at is not None and run.finished_at >= moment


async def test_runtime_streams_engine_events(clean_db: Database) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    async with _started(app):
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

    async with _started(app):
        for _ in range(200):
            if await journal_size() == 0:
                break
            await asyncio.sleep(0.01)
        assert await journal_size() == 0


async def test_retention_failure_notifies_once(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_cfg())
    runtime = app.state.runtime
    runtime.retention_first_s = 0.0
    runtime.retention_s = 0.01
    calls = [0]

    async def broken(*args: object) -> dict[str, int]:
        calls[0] += 1
        raise ConnectionError("db down")

    monkeypatch.setattr(DbRetention, "purge", broken)
    async with _started(app):
        await until(lambda: calls[0] >= 3)
        assert await _notified(clean_db, "retention_failed") == 1
        assert "retention" not in runtime.supervisor._backoff


def _key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


async def _seed_tg_session(db: Database) -> None:
    async with db.sessions() as session, session.begin():
        session.add(
            TgSession(account_id=1, dc_id=2, date=0, auth_key=b"\x01" + b"k" * 60, user_id=7)
        )
        session.add(TgPeer(account_id=1, id=GAME, access_hash=42, type="bot"))


async def _tg_counts(db: Database) -> tuple[int, int]:
    async with db.sessions() as s:
        sessions = await s.scalar(select(func.count()).select_from(TgSession))
        peers = await s.scalar(select(func.count()).select_from(TgPeer))
    return int(sessions or 0), int(peers or 0)


@pytest.mark.parametrize("key", [None, "short"])
async def test_process_refuses_without_valid_key(
    clean_db: Database, key: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    # Review Focus 2: без ключа (или с испорченным) процесс с kurigram не стартует, сессии целы.
    await _seed_tg_session(clean_db)
    app = create_application(
        _cfg(
            transport="kurigram",
            tg_api_id=1,
            tg_api_hash=SecretStr("x"),
            secret_key=None if key is None else SecretStr(key),
        )
    )
    with pytest.raises(SecretKeyError):
        async with app.router.lifespan_context(app):
            pass
    assert "secrets.token_bytes(32)" in caplog.text
    assert await _tg_counts(clean_db) == (1, 1)


async def test_fake_transport_checks_present_key(
    clean_db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    # Транспорту fake сессии не нужны: без ключа он стартует (как все тесты выше), но заданный
    # ключ сверяется всегда.
    app = create_application(_cfg(secret_key=SecretStr("short")))
    with pytest.raises(SecretKeyError):
        async with app.router.lifespan_context(app):
            pass
    assert "secrets.token_bytes(32)" in caplog.text


async def test_process_refuses_with_wrong_key_keeps_sessions(
    clean_db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    await ensure_key(clean_db, SecretBox(secrets.token_bytes(32)), reset=False)
    await _seed_tg_session(clean_db)
    app = create_application(_cfg(secret_key=SecretStr(_key())))
    with pytest.raises(SecretKeyError):
        async with app.router.lifespan_context(app):
            pass
    assert "ключ не подходит к базе" in caplog.text
    assert await _tg_counts(clean_db) == (1, 1)
    assert app.state.runtime.host.status().engines == []


async def test_reset_flag_drops_sessions_and_starts(
    clean_db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    await ensure_key(clean_db, SecretBox(secrets.token_bytes(32)), reset=False)
    await _seed_tg_session(clean_db)
    key = _key()
    app = create_application(_cfg(secret_key=SecretStr(key), secret_key_reset=True))
    async with _started(app):
        assert await _tg_counts(clean_db) == (0, 0)
    assert "PYROBOT_SECRET_KEY_RESET" in caplog.text
    # Новый ключ записан: следующий старт без флага проходит.
    again = create_application(_cfg(secret_key=SecretStr(key)))
    async with _started(again):
        pass
