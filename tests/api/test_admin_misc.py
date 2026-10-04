from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select, update

from app.api.app import create_api
from app.db.audit import AUDIT_ACTIONS
from app.db.models import AuditRow, InviteRow, NotificationRow
from app.db.notifications import DbNotifier
from app.tools.users import promote, set_password
from tests.api.conftest import PASSWORD, Api

pytestmark = pytest.mark.db

BASE = "/api/v1/admin"


async def test_create_invite_uses_server_defaults_and_shows_token_once(api: Api) -> None:
    r = await api.client.get(f"{BASE}/server-settings")
    assert r.status_code == 200, r.text
    version = r.json()["version"]
    r = await api.client.patch(
        f"{BASE}/server-settings",
        headers=api.headers,
        json={
            "version": version,
            "changes": {"invites": {"default_ttl_h": 24, "default_max_accounts": 3}},
        },
    )
    assert r.status_code == 200, r.text

    r = await api.client.post(f"{BASE}/invites", headers=api.headers, json={"note": "Для коллеги"})
    assert r.status_code == 201, r.text
    body = r.json()
    token = body["token"]
    assert len(token) >= 40
    assert body["path"] == f"/invite/{token}"
    invite = body["invite"]
    assert set(invite) == {"id", "created_at", "expires_at", "max_accounts", "note", "expired"}
    assert invite["max_accounts"] == 3
    assert invite["note"] == "Для коллеги"
    assert invite["expired"] is False
    hours = datetime.fromisoformat(invite["expires_at"]) - datetime.fromisoformat(
        invite["created_at"]
    )
    assert timedelta(hours=23, minutes=59) < hours < timedelta(hours=24, minutes=1)

    listed = await api.client.get(f"{BASE}/invites")
    assert listed.status_code == 200
    assert listed.json() == [invite]
    assert token not in listed.text

    for payload in (
        {"ttl_h": 0},
        {"ttl_h": 721},
        {"max_accounts": 0},
        {"max_accounts": 1001},
        {"note": "x" * 129},
    ):
        bad = await api.client.post(f"{BASE}/invites", headers=api.headers, json=payload)
        assert bad.status_code == 422

    async with api.db.sessions() as session, session.begin():
        await session.execute(
            update(InviteRow)
            .where(InviteRow.id == invite["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    listed = await api.client.get(f"{BASE}/invites")
    assert listed.json()[0]["expired"] is True


async def test_revoke_invite_then_accept_is_410(api: Api) -> None:
    created = await api.client.post(f"{BASE}/invites", headers=api.headers, json={})
    assert created.status_code == 201, created.text
    invite_id = created.json()["invite"]["id"]
    token = created.json()["token"]
    missing = await api.client.delete(f"{BASE}/invites/999999", headers=api.headers)
    assert missing.status_code == 404 and missing.json()["detail"] == "invite_not_found"
    revoked = await api.client.delete(f"{BASE}/invites/{invite_id}", headers=api.headers)
    assert revoked.status_code == 204
    assert (await api.client.get(f"{BASE}/invites")).json() == []
    again = await api.client.delete(f"{BASE}/invites/{invite_id}", headers=api.headers)
    assert again.status_code == 410 and again.json()["detail"] == "invite_gone"
    accepted = await api.client.post(
        f"/api/v1/invites/{token}/accept",
        json={"login": "revoked_user", "password": PASSWORD},
    )
    assert accepted.status_code == 410 and accepted.json()["detail"] == "invite_gone"


async def test_revoke_expired_invite_removes_it_from_list(api: Api) -> None:
    created = await api.client.post(f"{BASE}/invites", headers=api.headers, json={})
    assert created.status_code == 201, created.text
    invite_id = created.json()["invite"]["id"]
    async with api.db.sessions() as session, session.begin():
        await session.execute(
            update(InviteRow)
            .where(InviteRow.id == invite_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    listed = await api.client.get(f"{BASE}/invites")
    assert [i["id"] for i in listed.json()] == [invite_id]
    assert listed.json()[0]["expired"] is True

    revoked = await api.client.delete(f"{BASE}/invites/{invite_id}", headers=api.headers)
    assert revoked.status_code == 204, revoked.text
    assert (await api.client.get(f"{BASE}/invites")).json() == []


async def test_server_settings_patch_conflict_and_audit(api: Api) -> None:
    r = await api.client.get(f"{BASE}/server-settings")
    assert r.status_code == 200, r.text
    original = r.json()
    assert original["values"]["retention"]["audit_days"] == 365
    assert original["defaults"]["invites"]["default_ttl_h"] == 72
    assert "properties" in original["schema"]
    version = original["version"]

    changed = await api.client.patch(
        f"{BASE}/server-settings",
        headers=api.headers,
        json={"version": version, "changes": {"retention": {"audit_days": 400}}},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == version + 1
    assert changed.json()["values"]["retention"]["audit_days"] == 400
    assert changed.json()["changed"] == {"retention.audit_days": [365, 400]}

    conflict = await api.client.patch(
        f"{BASE}/server-settings",
        headers=api.headers,
        json={"version": version, "changes": {"retention": {"audit_days": 401}}},
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": {"code": "version_conflict", "version": version + 1}}

    for changes, path in [
        ({"unknown": 1}, "unknown"),
        ({"retention": {"audit_days": 0}}, "retention.audit_days"),
    ]:
        bad = await api.client.patch(
            f"{BASE}/server-settings",
            headers=api.headers,
            json={"version": version + 1, "changes": changes},
        )
        assert bad.status_code == 422, bad.text
        assert bad.json()["detail"][0]["loc"] == ["body", "changes", *path.split(".")]

    async with api.db.sessions() as session:
        entries = list(
            await session.scalars(
                select(AuditRow).where(AuditRow.action == "server_settings_changed")
            )
        )
    assert len(entries) == 1
    assert entries[0].details == {"changes": {"retention.audit_days": [365, 400]}}


async def test_server_settings_patch_applies_limits_live(api: Api) -> None:
    current = (await api.client.get(f"{BASE}/server-settings")).json()
    r = await api.client.patch(
        f"{BASE}/server-settings",
        headers=api.headers,
        json={"version": current["version"], "changes": {"limits": {"max_accounts_total": 1}}},
    )
    assert r.status_code == 200, r.text
    created = await api.client.post(
        "/api/v1/accounts", headers=api.headers, json={"name": "second"}
    )
    assert created.status_code == 409
    assert created.json()["detail"] == "server_full"


async def test_audit_pages(api: Api) -> None:
    for n in range(3):
        r = await api.client.post(f"{BASE}/invites", headers=api.headers, json={"note": str(n)})
        assert r.status_code == 201, r.text

    first = await api.client.get(f"{BASE}/audit", params={"limit": 2})
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["items"]) == 2
    assert body["items"][0]["id"] > body["items"][1]["id"]
    assert body["next_before"] == body["items"][-1]["id"]
    assert set(body["items"][0]) == {
        "id",
        "at",
        "actor_user_id",
        "actor_login",
        "action",
        "target_type",
        "target_id",
        "details",
    }
    second = await api.client.get(
        f"{BASE}/audit", params={"limit": 2, "before": body["next_before"]}
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 1
    assert second.json()["next_before"] is None
    for limit in (0, 101):
        assert (await api.client.get(f"{BASE}/audit", params={"limit": limit})).status_code == 422


async def test_server_notifications_list_and_read(api: Api) -> None:
    assert api.container.server_notifier is not None
    await api.container.server_notifier.notify("error", "account_error", "server event")
    await DbNotifier(api.db, 1).notify("warn", "account_blocked", "private account text")
    r = await api.client.get(f"{BASE}/notifications", params={"limit": 1})
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1
    item = r.json()[0]
    assert set(item) == {"id", "created_at", "level", "code", "text", "read"}
    assert item["code"] == "account_error"
    assert item["text"] == "server event"
    assert item["read"] is False
    read = await api.client.post(
        f"{BASE}/notifications/read", headers=api.headers, json={"up_to_id": item["id"]}
    )
    assert read.status_code == 204
    assert (await api.client.get(f"{BASE}/notifications")).json()[0]["read"] is True
    async with api.db.sessions() as session:
        account_notice = await session.scalar(
            select(NotificationRow).where(NotificationRow.account_id == 1)
        )
    assert account_notice is not None and account_notice.read is False
    for limit in (0, 101):
        assert (
            await api.client.get(f"{BASE}/notifications", params={"limit": limit})
        ).status_code == 422


async def test_every_audit_action_is_written(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    def ok(response: Response) -> None:
        assert response.status_code in {200, 201, 202, 204}, response.text

    created = await api.client.post(f"{BASE}/invites", headers=api.headers, json={})
    ok(created)
    ok(
        await api.client.delete(
            f"{BASE}/invites/{created.json()['invite']['id']}", headers=api.headers
        )
    )
    created = await api.client.post(f"{BASE}/invites", headers=api.headers, json={})
    ok(created)

    async with AsyncClient(
        transport=ASGITransport(app=create_api(api.container)), base_url="http://test"
    ) as invitee:
        accepted = await invitee.post(
            f"/api/v1/invites/{created.json()['token']}/accept",
            json={"login": "audit_user", "password": PASSWORD},
        )
        ok(accepted)
        user = await api.container.users.by_login("audit_user")
        assert user is not None
        user_id = user.id
        reissued = await invitee.post(
            "/api/v1/auth/recovery-codes",
            headers={"X-CSRF-Token": accepted.json()["csrf_token"]},
            json={"password": PASSWORD},
        )
        ok(reissued)
        codes = reissued.json()["codes"]
        ok(
            await invitee.post(
                "/api/v1/auth/recover/finish",
                json={
                    "login": "audit_user",
                    "recovery_code": codes[0],
                    "password": "new-password-1234",
                },
            )
        )

    passwords = iter(["cli-password-1234", "cli-password-1234"])
    monkeypatch.setattr("app.tools.users.getpass.getpass", lambda _: next(passwords))
    assert await set_password(api.db, "audit_user") == 0
    assert await promote(api.db, "audit_user") == 0

    ok(
        await api.client.patch(
            f"{BASE}/users/{user_id}", headers=api.headers, json={"max_accounts": 5}
        )
    )
    ok(
        await api.client.patch(
            f"{BASE}/users/{user_id}",
            headers=api.headers,
            json={"disabled": True, "reason": "test"},
        )
    )
    ok(
        await api.client.patch(
            f"{BASE}/users/{user_id}", headers=api.headers, json={"disabled": False}
        )
    )

    account = await api.client.post(
        "/api/v1/accounts", headers=api.headers, json={"name": "AuditAccount"}
    )
    ok(account)
    account_id = account.json()["id"]
    ok(
        await api.client.patch(
            f"{BASE}/accounts/{account_id}",
            headers=api.headers,
            json={"blocked": True, "reason": "test"},
        )
    )
    ok(
        await api.client.patch(
            f"{BASE}/accounts/{account_id}", headers=api.headers, json={"blocked": False}
        )
    )
    ok(
        await api.client.request(
            "DELETE",
            f"{BASE}/accounts/{account_id}",
            headers=api.headers,
            json={"confirm_name": "AuditAccount"},
        )
    )
    settings = (await api.client.get(f"{BASE}/server-settings")).json()
    ok(
        await api.client.patch(
            f"{BASE}/server-settings",
            headers=api.headers,
            json={"version": settings["version"], "changes": {"retention": {"audit_days": 400}}},
        )
    )
    ok(
        await api.client.request(
            "DELETE",
            f"{BASE}/users/{user_id}",
            headers=api.headers,
            json={"confirm_login": "audit_user"},
        )
    )

    audit = await api.client.get(f"{BASE}/audit", params={"limit": 100})
    ok(audit)
    assert {entry["action"] for entry in audit.json()["items"]} == AUDIT_ACTIONS
