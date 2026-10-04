import pytest
from httpx import AsyncClient

from app.api.container import Container
from app.api.deps import COOKIE
from app.db.audit import CLI_ACTOR
from tests.api.conftest import PASSWORD, login, make_user

pytestmark = pytest.mark.db


async def test_register_returns_codes_once_and_logs_in(
    api_client: AsyncClient, container: Container
) -> None:
    token, _info = await container.invites.create(
        CLI_ACTOR, ttl_h=24, max_accounts=2, note="invite-test"
    )

    # 1. GET invite peek -> 200
    peek_resp = await api_client.get(f"/api/v1/invites/{token}")
    assert peek_resp.status_code == 200
    peek_data = peek_resp.json()
    assert "expires_at" in peek_data

    # 2. POST accept -> 201
    reg_resp = await api_client.post(
        f"/api/v1/invites/{token}/accept",
        json={"login": "newuser", "password": "strong_password_123"},
    )
    assert reg_resp.status_code == 201
    reg_data = reg_resp.json()
    assert reg_data["login"] == "newuser"
    assert reg_data["role"] == "user"
    assert "csrf_token" in reg_data
    codes = reg_data["recovery_codes"]
    assert len(codes) == 10
    for code in codes:
        parts = code.split("-")
        assert len(parts) == 3
        assert parts[0].isdigit()

    assert COOKIE in api_client.cookies

    # 3. GET /api/v1/auth/me with cookie -> 200 user
    me_resp = await api_client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["login"] == "newuser"
    assert me_resp.json()["role"] == "user"

    # 4. Accept again -> 410 invite_gone
    reg_again = await api_client.post(
        f"/api/v1/invites/{token}/accept",
        json={"login": "otheruser", "password": "strong_password_123"},
    )
    assert reg_again.status_code == 410
    assert reg_again.json()["detail"] == "invite_gone"

    # 5. Peek again -> 410 invite_gone
    peek_again = await api_client.get(f"/api/v1/invites/{token}")
    assert peek_again.status_code == 410
    assert peek_again.json()["detail"] == "invite_gone"


async def test_login_taken_409(api_client: AsyncClient, container: Container) -> None:
    await make_user(container, "alice", role="user")
    token, _ = await container.invites.create(CLI_ACTOR, ttl_h=24, max_accounts=1, note=None)

    resp = await api_client.post(
        f"/api/v1/invites/{token}/accept",
        json={"login": "alice", "password": "password_12345"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "login_taken"


async def test_accept_rate_limited_by_ip(api_client: AsyncClient, container: Container) -> None:
    codes = [
        (
            await api_client.post(
                "/api/v1/invites/nonexistent-token/accept",
                json={"login": "someuser", "password": "password_12345"},
            )
        ).status_code
        for _ in range(7)
    ]
    assert codes == [404] * 6 + [429]


async def test_invite_used_server_notification_and_audit(
    api_client: AsyncClient, container: Container
) -> None:
    token, info = await container.invites.create(
        CLI_ACTOR, ttl_h=24, max_accounts=1, note="notif_test"
    )

    resp = await api_client.post(
        f"/api/v1/invites/{token}/accept",
        json={"login": "bob_user", "password": "password_12345"},
    )
    assert resp.status_code == 201

    # Check server notification
    assert container.server_notifier is not None
    notifs = await container.server_notifier.recent()
    used_notif = next((n for n in notifs if n.code == "invite_used"), None)
    assert used_notif is not None
    assert used_notif.level == "info"
    assert used_notif.text == f"invite {info.id} used by bob_user"

    # Check audit log
    assert container.audit is not None
    entries, _ = await container.audit.page(limit=10)
    user_reg = next((e for e in entries if e.action == "user_registered"), None)
    assert user_reg is not None
    assert user_reg.actor_login == "bob_user"


async def test_reissue_codes_requires_password(
    api_client: AsyncClient, container: Container
) -> None:
    await make_user(container, "charlie", password=PASSWORD)
    csrf = await login(api_client, login="charlie", password=PASSWORD)

    # 1. Wrong password -> 403 invalid_password
    resp_bad = await api_client.post(
        "/api/v1/auth/recovery-codes",
        headers={"X-CSRF-Token": csrf},
        json={"password": "wrong_password"},
    )
    assert resp_bad.status_code == 403
    assert resp_bad.json()["detail"] == "invalid_password"

    # 2. Correct password -> 200 {"codes": [...]}
    resp_ok = await api_client.post(
        "/api/v1/auth/recovery-codes",
        headers={"X-CSRF-Token": csrf},
        json={"password": PASSWORD},
    )
    assert resp_ok.status_code == 200
    codes = resp_ok.json()["codes"]
    assert len(codes) == 10
    for code in codes:
        parts = code.split("-")
        assert len(parts) == 3

    # Check audit log
    assert container.audit is not None
    entries, _ = await container.audit.page(limit=10)
    reissued = next((e for e in entries if e.action == "recovery_codes_reissued"), None)
    assert reissued is not None
    assert reissued.actor_login == "charlie"

    # 3. Rate limiter on session: failed attempts lead to 429
    bad_codes = [
        (
            await api_client.post(
                "/api/v1/auth/recovery-codes",
                headers={"X-CSRF-Token": csrf},
                json={"password": "wrong"},
            )
        ).status_code
        for _ in range(7)
    ]
    assert bad_codes == [403] * 6 + [429]
