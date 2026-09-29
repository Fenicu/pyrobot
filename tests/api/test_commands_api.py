import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.api.container import Container
from app.api.routes_commands import ConfirmRequiredOut
from app.db.actions import DbActionStore
from app.db.base import Database
from app.db.models import ActionRow
from app.engine.facade import EngineFacade
from app.engine.settings import EngineSection, Settings, StaticSettings
from app.engine.state.model import CharacterState, Obs, dump_state
from app.engine.transport.fake import FakeTransport, Sent
from app.engine.types import Button
from tests.api.conftest import login
from tests.engine.helpers import GAME, make_msg, until
from tests.engine.test_facade import build

pytestmark = pytest.mark.db

FAST = EngineSection(min_request_interval_s=0.0, default_expect_timeout_s=2.0)
LIVE = Settings(engine=FAST.model_copy(update={"mode": "live"}))
DRY = Settings(engine=FAST)


@pytest.fixture
async def running(container: Container) -> AsyncIterator[list[asyncio.Task[None]]]:
    tasks: list[asyncio.Task[None]] = []
    yield tasks
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _start(
    container: Container,
    db: Database,
    settings: Settings,
    tasks: list[asyncio.Task[None]],
    company: str | None = None,
) -> tuple[EngineFacade, FakeTransport]:
    snapshot = None
    if company is not None:
        seen = Obs(value=company, at=datetime.now(UTC))
        snapshot = dump_state(CharacterState(company=seen))
    f = build(settings=StaticSettings(settings), store=DbActionStore(db, 1), snapshot=snapshot)
    await f.pipeline.load()
    container.facade = f
    tasks.append(asyncio.create_task(f.gateway.run()))
    transport = f.gateway._transport
    assert isinstance(transport, FakeTransport)
    return f, transport


def _reply(f: EngineFacade, text: str, msg_id: int = 900) -> object:
    async def responder(rec: Sent) -> None:
        await f.pipeline.process(make_msg(text, msg_id=msg_id))

    return responder


async def _send(
    client: AsyncClient, h: dict[str, str], text: str, key: str, **kw: object
) -> tuple[int, dict[str, object]]:
    r = await client.post(
        "/api/v1/commands/send", headers=h, json={"text": text, "idempotency_key": key, **kw}
    )
    return r.status_code, r.json()


async def _rows(db: Database) -> list[ActionRow]:
    async with db.sessions() as s:
        return list(await s.scalars(select(ActionRow).order_by(ActionRow.id)))


async def test_commands_need_engine_and_csrf(
    container: Container, api_client: AsyncClient
) -> None:
    csrf = await login(api_client)
    body = {"text": "😎Я", "idempotency_key": "k"}
    assert (await api_client.post("/api/v1/commands/send", json=body)).status_code == 403
    h = {"X-CSRF-Token": csrf}
    assert (
        await api_client.post("/api/v1/commands/send", headers=h, json=body)
    ).status_code == 503
    bad_key = {"text": "😎Я", "idempotency_key": "a b"}
    container.facade = build()
    r = await api_client.post("/api/v1/commands/send", headers=h, json=bad_key)
    assert r.status_code == 422


async def test_nav_send_is_idempotent(
    container: Container,
    api_client: AsyncClient,
    clean_db: Database,
    running: list[asyncio.Task[None]],
) -> None:
    _, transport = await _start(container, clean_db, DRY, running)
    h = {"X-CSRF-Token": await login(api_client)}
    code, first = await _send(api_client, h, "😎Я", "k1")
    assert code == 200 and (first["status"], first["reason"]) == ("confirmed", "sent")
    code, again = await _send(api_client, h, "😎Я", "k1")
    assert code == 200 and again == first
    assert [s.payload for s in transport.sent] == ["😎Я"]
    rows = await _rows(clean_db)
    assert [(r.source, r.idempotency_key) for r in rows] == [("manual", "manual:k1")]
    code, _ = await _send(api_client, h, "/full", "k1")
    assert code == 422


async def test_dry_run_result_kept_for_key(
    container: Container,
    api_client: AsyncClient,
    clean_db: Database,
    running: list[asyncio.Task[None]],
) -> None:
    f, transport = await _start(container, clean_db, DRY, running)
    h = {"X-CSRF-Token": await login(api_client)}
    code, first = await _send(api_client, h, "/job", "k2")
    assert (code, first["status"], first["reason"]) == (200, "suppressed", "dry_run")
    await f.patch_settings({"engine": {"mode": "live"}}, version=0, by="t", confirm_live=True)
    code, again = await _send(api_client, h, "/job", "k2")
    assert again == first and transport.sent == []


async def test_forbidden_and_donate_always_403(
    container: Container,
    api_client: AsyncClient,
    clean_db: Database,
    running: list[asyncio.Task[None]],
) -> None:
    _, transport = await _start(container, clean_db, LIVE, running)
    h = {"X-CSRF-Token": await login(api_client)}
    for _ in range(2):
        code, body = await _send(api_client, h, "/changecompany", "k3")
        assert code == 403 and body == {"detail": "forbidden"}
    code, body = await _send(api_client, h, "/finish", "k4", confirm_token="x")
    assert code == 403 and body == {"detail": "donate"}
    click = {
        "chat_id": GAME,
        "message_id": 1,
        "revision": 0,
        "callback_data": "maze_buf_coins_strong",
        "idempotency_key": "k5",
    }
    r = await api_client.post("/api/v1/commands/click", headers=h, json=click)
    assert r.status_code == 403 and r.json() == {"detail": "donate"}
    assert transport.sent == []
    rows = await _rows(clean_db)
    assert {(r.status, r.reason, r.source, r.idempotency_key) for r in rows} == {
        ("rejected", "forbidden", "manual", None),
        ("rejected", "donate", "manual", None),
    }


async def test_risky_needs_confirm_token(
    container: Container,
    api_client: AsyncClient,
    clean_db: Database,
    running: list[asyncio.Task[None]],
) -> None:
    f, transport = await _start(container, clean_db, LIVE, running)
    transport.responder = _reply(f, "Ты улучшил навык")  # type: ignore[assignment]
    h = {"X-CSRF-Token": await login(api_client)}
    code, body = await _send(api_client, h, "/ucon", "r1")
    detail = body["detail"]
    assert code == 409 and isinstance(detail, dict)
    # Ответ совпадает со схемой 409 в OpenAPI (оболочка `detail`).
    ConfirmRequiredOut.model_validate(body)
    assert (detail["code"], detail["reason"], detail["command_class"]) == (
        "confirm_required",
        "missing",
        "risky",
    )
    token = detail["confirm_token"]
    code, body = await _send(api_client, h, "/ucon", "r2", confirm_token=token)
    assert code == 409 and body["detail"]["reason"] == "invalid"  # type: ignore[index]
    assert transport.sent == []
    code, body = await _send(api_client, h, "/ucon", "r1", confirm_token=token)
    assert code == 200 and (body["status"], body["reason"]) == ("confirmed", "reply")
    # Повтор после исполнения не требует нового подтверждения.
    code, again = await _send(api_client, h, "/ucon", "r1")
    assert code == 200 and again == body
    assert [s.payload for s in transport.sent] == ["/ucon"]


async def test_own_company_stock_needs_confirm(
    container: Container,
    api_client: AsyncClient,
    clean_db: Database,
    running: list[asyncio.Task[None]],
) -> None:
    # Своя компания — из профиля: её акции вручную только с подтверждением, чужие — сразу.
    f, transport = await _start(container, clean_db, LIVE, running, company="bmesa")
    transport.responder = _reply(f, "Куплено акций")  # type: ignore[assignment]
    h = {"X-CSRF-Token": await login(api_client)}
    code, body = await _send(api_client, h, "/buys_bmesa_5", "s1")
    assert code == 409 and body["detail"]["command_class"] == "risky"  # type: ignore[index]
    code, body = await _send(api_client, h, "/buys_stark_5", "s2")
    assert code == 200 and body["status"] == "confirmed"
    assert [s.payload for s in transport.sent] == ["/buys_stark_5"]


async def test_slow_command_is_pending_then_final(
    container: Container,
    api_client: AsyncClient,
    clean_db: Database,
    running: list[asyncio.Task[None]],
) -> None:
    f, transport = await _start(container, clean_db, LIVE, running)
    container.command_wait_s = 0.05
    h = {"X-CSRF-Token": await login(api_client)}
    code, body = await _send(api_client, h, "/job", "p1")
    assert code == 202 and body == {
        "action_id": None,
        "status": "pending",
        "reason": "",
        "answer": None,
    }
    await until(lambda: bool(transport.sent))
    code, body = await _send(api_client, h, "/job", "p1")
    assert code == 202
    await f.pipeline.process(make_msg("❗️Ты занят другим делом ещё 5 мин.", msg_id=901))
    await until(lambda: not f.manual_pending("p1"))
    code, body = await _send(api_client, h, "/job", "p1")
    assert code == 200 and (body["status"], body["reason"]) == ("refused", "busy")
    assert body["action_id"] is not None and len(transport.sent) == 1


async def test_click_checks_revision(
    container: Container,
    api_client: AsyncClient,
    clean_db: Database,
    running: list[asyncio.Task[None]],
) -> None:
    f, transport = await _start(container, clean_db, DRY, running)
    btn = (
        Button("🗡", 0, 0, data="gorbushka_fight"),
        Button("✖️", 0, 1, data="cancel_inline"),
    )
    await f.pipeline.process(make_msg("встреча", msg_id=50, revision=3, buttons=btn))
    h = {"X-CSRF-Token": await login(api_client)}

    async def click(data: str, revision: int, key: str) -> dict[str, object]:
        body = {
            "chat_id": GAME,
            "message_id": 50,
            "revision": revision,
            "callback_data": data,
            "idempotency_key": key,
        }
        r = await api_client.post("/api/v1/commands/click", headers=h, json=body)
        assert r.status_code == 200, r.text
        result: dict[str, object] = r.json()
        return result

    stale = await click("cancel_inline", 2, "c1")
    assert (stale["status"], stale["reason"]) == ("rejected", "stale_revision")
    assert (await click("gorbushka_fight", 3, "c2"))["reason"] == "dry_run"
    nav = await click("cancel_inline", 3, "c3")
    assert (nav["status"], nav["reason"]) == ("confirmed", "sent")
    assert [(s.kind, s.payload, s.message_id) for s in transport.sent] == [
        ("click", "cancel_inline", 50)
    ]


async def test_inflight_key_with_other_text_is_422(
    container: Container,
    api_client: AsyncClient,
    clean_db: Database,
    running: list[asyncio.Task[None]],
) -> None:
    _, transport = await _start(container, clean_db, LIVE, running)
    container.command_wait_s = 0.05
    h = {"X-CSRF-Token": await login(api_client)}
    code, _ = await _send(api_client, h, "/job", "f1")
    assert code == 202
    code, body = await _send(api_client, h, "/harvest", "f1")
    assert code == 422 and body == {"detail": "idempotency_key reused"}
    code, _ = await _send(api_client, h, "/job", "f1")
    assert code == 202
    assert [s.payload for s in transport.sent] == ["/job"]


class _BrokenStore(DbActionStore):
    async def create(self, *args: object, **kwargs: object) -> int:
        raise ConnectionError("db down")


async def test_unstored_manual_key_is_503(
    container: Container,
    api_client: AsyncClient,
    clean_db: Database,
    running: list[asyncio.Task[None]],
) -> None:
    f, transport = await _start(container, clean_db, DRY, running)
    f.gateway._store = _BrokenStore(clean_db, 1)
    h = {"X-CSRF-Token": await login(api_client)}
    code, body = await _send(api_client, h, "/job", "s1")
    assert code == 503 and body == {"detail": "store_failed"}
    code, body = await _send(api_client, h, "😎Я", "s2")
    assert code == 503 and transport.sent == []


async def test_key_reused_after_first_finished_in_between_is_422(
    container: Container,
    api_client: AsyncClient,
    clean_db: Database,
    running: list[asyncio.Task[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    f, transport = await _start(container, clean_db, DRY, running)
    h = {"X-CSRF-Token": await login(api_client)}
    code, first = await _send(api_client, h, "/job", "w1")
    assert (code, first["status"]) == (200, "suppressed")
    # Первое действие завершилось между проверкой «в полёте» и отправкой второго.
    monkeypatch.setattr(f, "manual_pending", lambda key: True)
    code, body = await _send(api_client, h, "/harvest", "w1")
    assert code == 422 and body == {"detail": "idempotency_key reused"}
    code, again = await _send(api_client, h, "/job", "w1")
    assert code == 200 and again == first
    assert transport.sent == []
