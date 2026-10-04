import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.app import create_api
from app.api.deps import COOKIE
from app.db.audit import Actor
from app.db.models import Account, AuditRow
from app.engine.stream import EventStream
from tests.api.conftest import PASSWORD, Api, login, make_user, run_engine
from tests.api.sse import read_sse
from tests.engine.helpers import until
from tests.engine.test_facade import build

pytestmark = pytest.mark.db


async def test_admin_is_404_for_user_role(api: Api) -> None:
    # Все пути /api/v1/admin/* (из openapi) для учётки user — 404.
    app = create_api(api.container)
    openapi = app.openapi()
    admin_routes = [
        (method.upper(), path)
        for path, ops in openapi["paths"].items()
        if path.startswith("/api/v1/admin")
        for method in ops
    ]
    assert admin_routes, "expected admin routes in openapi"

    bob_id = await make_user(api.container, "bob", role="user")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as user_client:
        csrf = await login(user_client, login="bob")
        user_headers = {"X-CSRF-Token": csrf}

        for method, path in admin_routes:
            target = path.replace("{user_id}", str(bob_id))
            resp = await user_client.request(
                method,
                target,
                headers=user_headers,
                json={"confirm_login": "bob", "max_accounts": 5, "disabled": True},
            )
            msg = f"{method} {target} expected 404, got {resp.status_code}"
            assert resp.status_code == 404, msg
            assert resp.json()["detail"] == "not found"


async def test_list_users_service_fields(api: Api) -> None:
    # GET /api/v1/admin/users возвращает список пользователей со служебными полями.
    bob_id = await make_user(api.container, "bob", role="user")
    # Добавим аккаунт для bob
    async with api.db.sessions() as s, s.begin():
        s.add(Account(owner_id=bob_id, name="BobAcc", status="enabled"))
        s.add(Account(owner_id=bob_id, name="BobDeleting", status="deleting"))

    resp = await api.client.get("/api/v1/admin/users", headers=api.headers)
    assert resp.status_code == 200, resp.text
    users = resp.json()
    assert len(users) >= 2

    admin_user = next(u for u in users if u["login"] == "admin")
    assert admin_user["role"] == "owner"
    assert admin_user["disabled"] is False
    assert admin_user["deleting"] is False
    assert "password" not in admin_user
    assert "password_hash" not in admin_user

    bob_user = next(u for u in users if u["login"] == "bob")
    assert bob_user["id"] == bob_id
    assert bob_user["role"] == "user"
    assert bob_user["accounts"] == 1  # status != 'deleting'
    assert bob_user["max_accounts"] == 10
    assert bob_user["disabled"] is False
    assert bob_user["disabled_reason"] is None
    assert bob_user["deleting"] is False

    expected_fields = {
        "id",
        "login",
        "role",
        "created_at",
        "last_login_at",
        "accounts",
        "max_accounts",
        "disabled",
        "disabled_reason",
        "deleting",
    }
    for u in users:
        assert set(u.keys()) == expected_fields


async def test_change_limit_audited(api: Api) -> None:
    bob_id = await make_user(api.container, "bob", role="user")

    resp = await api.client.patch(
        f"/api/v1/admin/users/{bob_id}",
        headers=api.headers,
        json={"max_accounts": 5},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["max_accounts"] == 5

    user = await api.container.users.get(bob_id)
    assert user is not None and user.max_accounts == 5

    # Проверка журнала аудита
    async with api.db.sessions() as s:
        stmt = (
            select(AuditRow)
            .where(AuditRow.action == "user_limit_changed", AuditRow.target_id == bob_id)
            .order_by(AuditRow.id.desc())
        )
        entry = (await s.scalars(stmt)).first()
        assert entry is not None
        assert entry.target_type == "user"
        assert entry.details == {"from": 10, "to": 5}


async def test_disable_last_owner_409(api: Api) -> None:
    # Получаем id владельца admin
    admin = await api.container.users.by_login("admin")
    assert admin is not None

    # Попытка отключить последнего владельца -> 409 last_owner
    patch_resp = await api.client.patch(
        f"/api/v1/admin/users/{admin.id}",
        headers=api.headers,
        json={"disabled": True, "reason": "retire"},
    )
    assert patch_resp.status_code == 409
    assert patch_resp.json()["detail"] == "last_owner"

    # Попытка удалить последнего владельца -> 409 last_owner
    del_resp = await api.client.request(
        "DELETE",
        f"/api/v1/admin/users/{admin.id}",
        headers=api.headers,
        json={"confirm_login": "admin"},
    )
    assert del_resp.status_code == 409
    assert del_resp.json()["detail"] == "last_owner"


async def test_disabled_user_loses_sessions_sse_and_engines(api: Api) -> None:
    # Review Focus 2:
    # У bob открыт поток событий аккаунта 2 (FakeEngines), sse_heartbeat_s = 0.05;
    # PATCH disabled → поток закончился за ≤ 2 пинга, GET /accounts с его cookie — 401,
    # аккаунт 2 — disabled/user_disabled, poke был.
    app = create_api(api.container)
    api.container.sse_heartbeat_s = 0.05

    bob_id = await make_user(api.container, "bob", role="user")
    async with api.db.sessions() as s, s.begin():
        acc = Account(owner_id=bob_id, name="BobAcc", status="enabled")
        s.add(acc)
        await s.flush()
        acc_id = acc.id

    facade = build()
    stream = EventStream(epoch="e1", history=5)
    facade.stream = stream
    run_engine(api.container, facade, account_id=acc_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bob_client:
        await login(bob_client, login="bob")
        bob_cookie = {"cookie": f"{COOKIE}={bob_client.cookies[COOKIE]}"}

        pokes_before = api.engines.pokes

        async def read_stream() -> tuple[int, list[Any]]:
            return await read_sse(
                app,
                f"/api/v1/accounts/{acc_id}/events",
                headers=bob_cookie,
                count=100,
                timeout=2.0,
            )

        sse_task = asyncio.create_task(read_stream())
        await until(lambda: stream.subscribers == 1, 2.0)

        # Owner отключает bob
        resp = await api.client.patch(
            f"/api/v1/admin/users/{bob_id}",
            headers=api.headers,
            json={"disabled": True, "reason": "spam"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["disabled"] is True
        assert resp.json()["disabled_reason"] == "spam"

        # Поток событий должен завершиться быстро (<= 2 пинга, т.е. ~0.1-0.2 с)
        t0 = asyncio.get_running_loop().time()
        status, _ = await sse_task
        elapsed = asyncio.get_running_loop().time() - t0
        assert status == 200
        assert elapsed < 0.5, f"stream took too long to terminate: {elapsed}s"
        assert stream.subscribers == 0

        # Любой запрос с cookie bob — 401
        acc_resp = await bob_client.get("/api/v1/accounts")
        assert acc_resp.status_code == 401

        # Аккаунт переведён в disabled / user_disabled
        acc_info = await api.container.accounts.get(acc_id)
        assert acc_info is not None
        assert acc_info.status == "disabled"
        assert acc_info.status_reason == "user_disabled"

        # poke() был вызван
        assert api.engines.pokes > pokes_before

        # Проверка записи в аудит
        async with api.db.sessions() as s:
            stmt = select(AuditRow).where(
                AuditRow.action == "user_disabled", AuditRow.target_id == bob_id
            )
            entry = (await s.scalars(stmt)).first()
            assert entry is not None
            assert entry.details == {"reason": "spam"}


async def test_enable_user(api: Api) -> None:
    bob_id = await make_user(api.container, "bob", role="user")
    await api.container.users.disable(bob_id, "test")

    resp = await api.client.patch(
        f"/api/v1/admin/users/{bob_id}",
        headers=api.headers,
        json={"disabled": False},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["disabled"] is False
    assert data["disabled_reason"] is None

    # Проверка аудита
    async with api.db.sessions() as s:
        stmt = select(AuditRow).where(
            AuditRow.action == "user_enabled", AuditRow.target_id == bob_id
        )
        entry = (await s.scalars(stmt)).first()
        assert entry is not None


async def test_delete_user_202_login_taken_until_purged(api: Api) -> None:
    # DELETE → 202, вход — 401, accept приглашения с тем же логином — 409 login_taken;
    # AccountRepo.purge последнего аккаунта → строки users нет, логин свободен.
    carol_id = await make_user(api.container, "carol", role="user")
    async with api.db.sessions() as s, s.begin():
        acc = Account(owner_id=carol_id, name="CarolAcc", status="enabled")
        s.add(acc)
        await s.flush()
        acc_id = acc.id

    pokes_before = api.engines.pokes

    # DELETE пользователя
    del_resp = await api.client.request(
        "DELETE",
        f"/api/v1/admin/users/{carol_id}",
        headers=api.headers,
        json={"confirm_login": "carol"},
    )
    assert del_resp.status_code == 202, del_resp.text
    assert api.engines.pokes > pokes_before

    # Вход отключён / удаляется -> 401
    login_resp = await api.client.post(
        "/api/v1/auth/login",
        json={"login": "carol", "password": PASSWORD},
    )
    assert login_resp.status_code == 401

    # Создаём приглашение для проверки занятости логина
    admin = await api.container.users.by_login("admin")
    assert admin is not None
    token, _ = await api.container.invites.create(
        Actor(admin.id, admin.login),
        ttl_h=24,
        max_accounts=1,
        note=None,
    )

    # Принятие инвайта с логином carol -> 409 login_taken (строка пользователя ещё в базе)
    accept_resp = await api.client.post(
        f"/api/v1/invites/{token}/accept",
        json={"login": "carol", "password": "newpassword1234"},
    )
    assert accept_resp.status_code == 409
    assert accept_resp.json()["detail"] == "login_taken"

    # Теперь очищаем (purge) последний аккаунт carol
    await api.container.accounts.purge(acc_id)

    # Строка пользователя должна быть удалена
    assert await api.container.users.get(carol_id) is None

    # Теперь логин carol свободен и инвайт принимается!
    token2, _ = await api.container.invites.create(
        Actor(admin.id, admin.login),
        ttl_h=24,
        max_accounts=1,
        note=None,
    )
    accept_ok = await api.client.post(
        f"/api/v1/invites/{token2}/accept",
        json={"login": "carol", "password": "newpassword1234"},
    )
    assert accept_ok.status_code == 201, accept_ok.text

    # Проверка аудита
    async with api.db.sessions() as s:
        stmt = select(AuditRow).where(
            AuditRow.action == "user_deleted", AuditRow.target_id == carol_id
        )
        entry = (await s.scalars(stmt)).first()
        assert entry is not None


async def test_delete_user_without_accounts_removes_row_now(api: Api) -> None:
    # У пользователя без аккаунтов строка users удаляется сразу
    dave_id = await make_user(api.container, "dave", role="user")

    del_resp = await api.client.request(
        "DELETE",
        f"/api/v1/admin/users/{dave_id}",
        headers=api.headers,
        json={"confirm_login": "dave"},
    )
    assert del_resp.status_code == 202, del_resp.text
    assert await api.container.users.get(dave_id) is None


async def test_delete_confirm_login_mismatch_422(api: Api) -> None:
    user_id = await make_user(api.container, "eva", role="user")

    del_resp = await api.client.request(
        "DELETE",
        f"/api/v1/admin/users/{user_id}",
        headers=api.headers,
        json={"confirm_login": "wrong_login"},
    )
    assert del_resp.status_code == 422
    assert del_resp.json()["detail"] == "confirm_login_mismatch"


async def test_patch_and_delete_user_not_found_404(api: Api) -> None:
    patch_resp = await api.client.patch(
        "/api/v1/admin/users/999999",
        headers=api.headers,
        json={"max_accounts": 3},
    )
    assert patch_resp.status_code == 404
    assert patch_resp.json()["detail"] == "user_not_found"

    del_resp = await api.client.request(
        "DELETE",
        "/api/v1/admin/users/999999",
        headers=api.headers,
        json={"confirm_login": "someone"},
    )
    assert del_resp.status_code == 404
    assert del_resp.json()["detail"] == "user_not_found"
