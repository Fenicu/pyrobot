import asyncio
import logging
import re
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api import security
from app.api.app import create_api
from app.api.container import Container
from app.api.deps import COOKIE
from app.db.models import AuditRow, User
from app.db.notifications import DbNotifier
from app.engine.transport.fake import FakeTransport
from tests.api.conftest import Api, make_user, run_engine
from tests.engine.test_facade import build

pytestmark = pytest.mark.db


async def _get_user(c: Container, login: str = "admin") -> User:
    async with c.db.sessions() as session:
        user = await session.scalar(select(User).where(User.login == login))
    assert user is not None
    return user


async def test_code_goes_to_saved_messages_of_online_accounts(api: Api) -> None:
    # Аккаунт 1 (онлайн, FakeTransport) и 2 (движок не запущен):
    t1 = FakeTransport()
    f1 = build(authorized=True, transport=t1)
    await f1.tg.boot()
    run_engine(api.container, f1, 1)

    # Создаём аккаунт 2 для admin
    user = await _get_user(api.container, "admin")
    await api.container.accounts.create(user.id, "account-2", capacity=10)

    resp = await api.client.post("/api/v1/auth/recover/start", json={"login": "admin"})
    assert resp.status_code == 202
    await api.container.drain()

    # Сообщение ушло только в Saved Messages аккаунта 1
    assert len(t1.saved) == 1
    assert "Код восстановления пароля pyrobot:" in t1.saved[0]

    # В обычный чат ничего не отправлялось
    assert len(t1.sent) == 0


async def test_start_same_response_for_unknown_login(api: Api) -> None:
    resp = await api.client.post("/api/v1/auth/recover/start", json={"login": "nobody"})
    assert resp.status_code == 202
    await api.container.drain()


async def test_start_rate_limit_per_login(api: Api) -> None:
    # Первые 3 запроса в час проходят
    for _ in range(3):
        resp = await api.client.post("/api/v1/auth/recover/start", json={"login": "admin"})
        assert resp.status_code == 202
    await api.container.drain()

    # 4-й запрос в тот же час блокируется по логину
    resp = await api.client.post("/api/v1/auth/recover/start", json={"login": "admin"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers and int(resp.headers["Retry-After"]) > 0


async def test_finish_with_telegram_code_closes_sessions(api: Api) -> None:
    t1 = FakeTransport()
    f1 = build(authorized=True, transport=t1)
    await f1.tg.boot()
    run_engine(api.container, f1, 1)

    user = await _get_user(api.container, "admin")
    await api.container.accounts.create(user.id, "account-2", capacity=10)

    old_cookie = api.client.cookies[COOKIE]

    # Запрашиваем код
    resp_start = await api.client.post("/api/v1/auth/recover/start", json={"login": "admin"})
    assert resp_start.status_code == 202
    await api.container.drain()

    match = re.search(r"\b\d{8}\b", t1.saved[0])
    assert match is not None
    code = match.group(0)

    new_pw = "new_strong_password_123"
    resp_finish = await api.client.post(
        "/api/v1/auth/recover/finish",
        json={"login": "admin", "code": code, "password": new_pw},
    )
    assert resp_finish.status_code == 200
    body = resp_finish.json()
    assert body["login"] == "admin" and body["role"] == "owner"

    new_cookie = api.client.cookies[COOKIE]
    assert new_cookie != old_cookie

    # Старая сессия закрыта
    app = create_api(api.container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={COOKIE: old_cookie}
    ) as old_client:
        assert (await old_client.get("/api/v1/auth/me")).status_code == 401

    # Новая сессия работает
    me_resp = await api.client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200

    # Уведомление password_recovered получено всеми аккаунтами
    n1 = await DbNotifier(api.db, 1).recent()
    assert any(n.code == "password_recovered" and "via telegram" in n.text for n in n1)

    accounts = await api.container.accounts.list_for_user(user.id)
    acc2 = next(a for a in accounts if a.id != 1)
    n2 = await DbNotifier(api.db, acc2.id).recent()
    assert any(n.code == "password_recovered" and "via telegram" in n.text for n in n2)

    # Запись в аудите
    async with api.db.sessions() as session:
        audit_rows = list(
            await session.scalars(select(AuditRow).where(AuditRow.action == "password_recovered"))
        )
    assert len(audit_rows) >= 1
    assert audit_rows[-1].details == {"method": "telegram"}


async def test_finish_with_recovery_code_one_time(api: Api) -> None:
    user = await _get_user(api.container, "admin")
    codes = await api.container.recovery_codes.issue(user.id)
    recovery_code = codes[0]

    new_pw = "recovery_pw_password_123"
    resp = await api.client.post(
        "/api/v1/auth/recover/finish",
        json={"login": "admin", "recovery_code": recovery_code, "password": new_pw},
    )
    assert resp.status_code == 200

    # Повторное использование того же кода восстановления не работает
    resp_again = await api.client.post(
        "/api/v1/auth/recover/finish",
        json={"login": "admin", "recovery_code": recovery_code, "password": new_pw},
    )
    assert resp_again.status_code == 403
    assert resp_again.json()["detail"] == "invalid_code"

    # Уведомления и аудит
    n1 = await DbNotifier(api.db, 1).recent()
    assert any(n.code == "password_recovered" and "via recovery_code" in n.text for n in n1)

    async with api.db.sessions() as session:
        audit_rows = list(
            await session.scalars(select(AuditRow).where(AuditRow.action == "password_recovered"))
        )
    assert len(audit_rows) >= 1
    assert audit_rows[-1].details == {"method": "recovery_code"}


async def test_finish_needs_exactly_one_code(api: Api) -> None:
    # Ни одного кода
    r1 = await api.client.post(
        "/api/v1/auth/recover/finish",
        json={"login": "admin", "password": "new_password_1234"},
    )
    assert r1.status_code == 422

    # Оба кода
    r2 = await api.client.post(
        "/api/v1/auth/recover/finish",
        json={
            "login": "admin",
            "code": "12345678",
            "recovery_code": "1-ABCDE-FGHIJ",
            "password": "new_password_1234",
        },
    )
    assert r2.status_code == 422


async def test_ten_bad_recovery_codes_lock_login_for_hour(api: Api) -> None:
    # Каждая попытка — с нового IP: лимитер по IP не срабатывает, остаётся лимит логина.
    app = create_api(api.container)
    bad_code = "1-WRONG-00000"
    body = {"login": "admin", "recovery_code": bad_code, "password": "new_password_1234"}

    async def attempt(i: int) -> Any:
        transport = ASGITransport(app=app, client=(f"10.0.0.{i}", 4000))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/v1/auth/recover/finish", json=body)

    for i in range(10):
        resp = await attempt(i)
        assert resp.status_code == 403
        assert resp.json()["detail"] == "invalid_code"

    # 11-я попытка блокируется на час
    resp11 = await attempt(10)
    assert resp11.status_code == 429
    assert int(resp11.headers.get("Retry-After", 0)) > 0


async def test_start_survives_disabled_offline_and_failing_accounts(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    # Review Focus 3:
    # Аккаунт 1: выключен (disabled)
    user = await _get_user(api.container, "admin")
    await api.container.accounts.set_status(1, "disabled", "testing")

    # Аккаунт 2: офлайн (Telegram unauthorized / not online)
    acc2 = await api.container.accounts.create(user.id, "account-offline", capacity=10)
    f2 = build(authorized=False)
    await f2.tg.boot()
    run_engine(api.container, f2, acc2.id)

    # Аккаунт 3: онлайн, но send_saved бросает исключение
    acc3 = await api.container.accounts.create(user.id, "account-failing", capacity=10)
    t3 = FakeTransport()
    t3.fail_with = [RuntimeError("tg transport crash")]
    f3 = build(authorized=True, transport=t3)
    await f3.tg.boot()
    run_engine(api.container, f3, acc3.id)

    with caplog.at_level(logging.ERROR):
        resp = await api.client.post("/api/v1/auth/recover/start", json={"login": "admin"})
        assert resp.status_code == 202
        await api.container.drain()

    assert "tg transport crash" in caplog.text or "background task failed" in caplog.text


class _CountingHasher:
    """argon2 процесса со счётчиком проверок."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.verifies = 0

    def verify(self, password_hash: str, password: str) -> bool:
        self.verifies += 1
        return bool(self._inner.verify(password_hash, password))

    def hash(self, password: str) -> str:
        return str(self._inner.hash(password))


@pytest.fixture
async def hasher(monkeypatch: pytest.MonkeyPatch) -> _CountingHasher:
    await security.dummy_hash()
    counting = _CountingHasher(security._hasher)
    monkeypatch.setattr(security, "_hasher", counting)
    return counting


@pytest.mark.parametrize("kind", ["code", "recovery_code"])
async def test_finish_failures_across_logins_reach_ip_limit(api: Api, kind: str) -> None:
    # Перебор с одного IP по разным логинам (есть такой, нет такого, отключённый) упирается в
    # лимитер по IP, а не обходит его.
    bob = await make_user(api.container, "bob")
    await api.container.users.disable(bob, None)
    secret = "12345678" if kind == "code" else "1-WRONG-00000"
    logins = ["admin", "nobody1", "bob", "nobody2", "nobody3", "nobody4"]
    for login_ in logins:
        r = await api.client.post(
            "/api/v1/auth/recover/finish",
            json={"login": login_, kind: secret, "password": "new_password_1234"},
        )
        assert (r.status_code, r.json()) == (403, {"detail": "invalid_code"}), login_
    r = await api.client.post(
        "/api/v1/auth/recover/finish",
        json={"login": "nobody5", kind: secret, "password": "new_password_1234"},
    )
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


@pytest.mark.parametrize("login_", ["admin", "nobody", "bob"])
async def test_finish_recovery_code_one_argon2_check_for_any_login(
    api: Api, hasher: _CountingHasher, login_: str
) -> None:
    bob = await make_user(api.container, "bob")
    await api.container.users.disable(bob, None)
    for code in ("1-WRONG-00000", "not-a-code"):
        before = hasher.verifies
        r = await api.client.post(
            "/api/v1/auth/recover/finish",
            json={"login": login_, "recovery_code": code, "password": "new_password_1234"},
        )
        assert (r.status_code, r.json()) == (403, {"detail": "invalid_code"})
        assert hasher.verifies - before == 1, code


@pytest.mark.parametrize("login_", ["nobody", "bob"])
async def test_finish_telegram_code_unknown_login_checks_like_known(
    api: Api, hasher: _CountingHasher, monkeypatch: pytest.MonkeyPatch, login_: str
) -> None:
    bob = await make_user(api.container, "bob")
    await api.container.users.disable(bob, None)
    requests = api.container.recovery_requests
    checked: list[int] = []
    real_check = requests.check

    async def check(user_id: int, code: str) -> bool:
        checked.append(user_id)
        return await real_check(user_id, code)

    monkeypatch.setattr(requests, "check", check)
    r = await api.client.post(
        "/api/v1/auth/recover/finish",
        json={"login": login_, "code": "12345678", "password": "new_password_1234"},
    )
    assert (r.status_code, r.json()) == (403, {"detail": "invalid_code"})
    # Как у существующей учётки: выборка запроса и HMAC, без argon2.
    assert len(checked) == 1 and checked[0] != bob
    assert hasher.verifies == 0


@pytest.mark.parametrize("login_", ["admin", "nobody"])
async def test_start_answers_before_any_lookup(
    api: Api, monkeypatch: pytest.MonkeyPatch, login_: str
) -> None:
    t1 = FakeTransport()
    f1 = build(authorized=True, transport=t1)
    await f1.tg.boot()
    run_engine(api.container, f1, 1)
    gate = asyncio.Event()
    looked_up: list[str] = []
    real_get_user = api.container.auth.get_user

    async def get_user(login: str) -> Any:
        looked_up.append(login)
        await gate.wait()
        return await real_get_user(login)

    monkeypatch.setattr(api.container.auth, "get_user", get_user)
    # Ответ не ждёт ни поиска учётки, ни записи запроса, ни отправки: время ответа не выдаёт,
    # есть ли такой логин.
    r = await asyncio.wait_for(
        api.client.post("/api/v1/auth/recover/start", json={"login": login_}), 5.0
    )
    assert r.status_code == 202
    gate.set()
    await api.container.drain()
    assert looked_up == [login_]
    assert len(t1.saved) == (1 if login_ == "admin" else 0)
