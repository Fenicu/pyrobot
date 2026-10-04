from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.app import create_api
from app.db.models import (
    Account,
    ActionRow,
    AuditRow,
    DecisionRow,
    LedgerRow,
    MessageRow,
    MetricRow,
    NotificationRow,
)
from tests.api.conftest import Api, login, make_user
from tests.engine.test_facade import build

pytestmark = pytest.mark.db


async def test_accounts_service_fields_and_load(api: Api) -> None:
    now = datetime.now(UTC)
    bob_id = await make_user(api.container, "bob", role="user")

    async with api.db.sessions() as s, s.begin():
        acc = Account(id=2, owner_id=bob_id, name="BobAcc", status="enabled")
        s.add(acc)
        await s.flush()

        # Сообщения: 1 свежее (< 1h) и 1 старое (> 1h)
        s.add(
            MessageRow(
                account_id=2,
                chat_id=1,
                msg_id=1,
                revision=1,
                content_hash="h1",
                kind="msg",
                date=now,
                received_at=now - timedelta(minutes=10),
                events=[],
            )
        )
        s.add(
            MessageRow(
                account_id=2,
                chat_id=1,
                msg_id=2,
                revision=1,
                content_hash="h2",
                kind="msg",
                date=now,
                received_at=now - timedelta(minutes=70),
                events=[],
            )
        )

        # Действия: 1 свежее (< 1h) и 1 старое (> 1h)
        s.add(
            ActionRow(
                account_id=2,
                source="test",
                kind="send",
                chat_id=1,
                payload={},
                command_class="action",
                status="confirmed",
                created_at=now - timedelta(minutes=15),
            )
        )
        s.add(
            ActionRow(
                account_id=2,
                source="test",
                kind="send",
                chat_id=1,
                payload={},
                command_class="action",
                status="confirmed",
                created_at=now - timedelta(minutes=80),
            )
        )

        # Решения, метрики, ledger, уведомления
        s.add(
            DecisionRow(
                account_id=2,
                at=now,
                kind="plan",
                params={},
                reason="test",
                candidates=[],
            )
        )
        s.add(
            MetricRow(
                account_id=2,
                ts=now,
                key="cpu",
                value=1.0,
            )
        )
        s.add(
            LedgerRow(
                account_id=2,
                at=now,
                day=now.date(),
                recorded_at=now,
                kind="reward",
                amounts={"gold": 10},
                items={},
                chat_id=1,
                msg_id=1,
                revision=1,
                content_hash="h1",
                seq=0,
            )
        )
        s.add(
            NotificationRow(
                account_id=2,
                level="warn",
                code="warn_code",
                text="test warn",
            )
        )

    resp = await api.client.get("/api/v1/admin/accounts", headers=api.headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert isinstance(items, list)

    bob_acc = next((a for a in items if a["id"] == 2), None)
    assert bob_acc is not None
    assert bob_acc["name"] == "BobAcc"
    assert bob_acc["owner_id"] == bob_id
    assert bob_acc["owner_login"] == "bob"
    assert bob_acc["status"] == "enabled"
    assert bob_acc["status_reason"] is None
    assert bob_acc["blocked"] is False
    assert bob_acc["blocked_reason"] is None
    assert bob_acc["running"] is False
    assert bob_acc["tg_online"] is False
    assert bob_acc["restarts_24h"] == 0
    assert bob_acc["last_error_code"] is None
    assert bob_acc["last_error_at"] is None

    # Нагрузка за 1 час
    assert bob_acc["messages_1h"] == 1
    assert bob_acc["actions_1h"] == 1

    # Строки по таблицам
    rows = bob_acc["rows"]
    assert rows["messages"] == 2
    assert rows["actions"] == 2
    assert rows["decisions"] == 1
    assert rows["metrics"] == 1
    assert rows["ledger"] == 1
    assert rows["notifications"] == 1


async def test_block_requires_reason_and_notifies_account(api: Api) -> None:
    bob_id = await make_user(api.container, "bob", role="user")
    acc = await api.container.accounts.create(bob_id, "BobToBlock")

    # 1. Блокировка без причины -> 422 reason_required
    r = await api.client.patch(
        f"/api/v1/admin/accounts/{acc.id}",
        json={"blocked": True},
        headers=api.headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "reason_required"

    # 2. Блокировка с пустой причиной -> 422 reason_required
    r = await api.client.patch(
        f"/api/v1/admin/accounts/{acc.id}",
        json={"blocked": True, "reason": "   "},
        headers=api.headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "reason_required"

    # 3. Причина длиннее 256 -> 422
    r = await api.client.patch(
        f"/api/v1/admin/accounts/{acc.id}",
        json={"blocked": True, "reason": "a" * 257},
        headers=api.headers,
    )
    assert r.status_code == 422

    # 4. Несуществующий аккаунт -> 404
    r = await api.client.patch(
        "/api/v1/admin/accounts/999999",
        json={"blocked": True, "reason": "test"},
        headers=api.headers,
    )
    assert r.status_code == 404

    # 5. Успешная блокировка с причиной
    pokes_before = api.container.engines.pokes
    r = await api.client.patch(
        f"/api/v1/admin/accounts/{acc.id}",
        json={"blocked": True, "reason": "Спам"},
        headers=api.headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["blocked"] is True
    assert data["blocked_reason"] == "Спам"
    assert data["status"] == "disabled"
    assert data["status_reason"] == "blocked_by_owner"
    assert api.container.engines.pokes > pokes_before

    # Аудит: account_blocked
    async with api.db.sessions() as s:
        audit = await s.scalar(
            select(AuditRow)
            .where(AuditRow.action == "account_blocked", AuditRow.target_id == acc.id)
            .order_by(AuditRow.id.desc())
        )
        assert audit is not None
        assert audit.details == {"reason": "Спам"}

        # Уведомление аккаунта: level warn, code account_blocked, text Спам
        notif = await s.scalar(
            select(NotificationRow)
            .where(NotificationRow.account_id == acc.id, NotificationRow.code == "account_blocked")
            .order_by(NotificationRow.id.desc())
        )
        assert notif is not None
        assert notif.level == "warn"
        assert notif.text == "Спам"

    # 6. Разблокировка
    pokes_before = api.container.engines.pokes
    r = await api.client.patch(
        f"/api/v1/admin/accounts/{acc.id}",
        json={"blocked": False},
        headers=api.headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["blocked"] is False
    assert data["blocked_reason"] is None
    # Статус остаётся disabled
    assert data["status"] == "disabled"
    assert api.container.engines.pokes > pokes_before

    # Аудит: account_unblocked
    async with api.db.sessions() as s:
        audit = await s.scalar(
            select(AuditRow)
            .where(AuditRow.action == "account_unblocked", AuditRow.target_id == acc.id)
            .order_by(AuditRow.id.desc())
        )
        assert audit is not None

        # Уведомление аккаунта: level info, code account_unblocked
        notif = await s.scalar(
            select(NotificationRow)
            .where(
                NotificationRow.account_id == acc.id, NotificationRow.code == "account_unblocked"
            )
            .order_by(NotificationRow.id.desc())
        )
        assert notif is not None
        assert notif.level == "info"
        assert notif.text == "account unblocked by owner"


@pytest.mark.parametrize("blocked", [True, False])
async def test_block_unblock_account_purged_midway_returns_404(
    api: Api, monkeypatch: pytest.MonkeyPatch, blocked: bool
) -> None:
    bob_id = await make_user(api.container, "bob", role="user")
    acc = await api.container.accounts.create(bob_id, "BobPurged")

    async def gone(*_args: object) -> None:
        raise KeyError(acc.id)

    monkeypatch.setattr(api.container.accounts, "block", gone)
    monkeypatch.setattr(api.container.accounts, "unblock", gone)

    body = {"blocked": True, "reason": "Спам"} if blocked else {"blocked": False}
    r = await api.client.patch(
        f"/api/v1/admin/accounts/{acc.id}",
        json=body,
        headers=api.headers,
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "account not found"


async def test_user_cannot_enable_blocked_account(api: Api) -> None:
    bob_id = await make_user(api.container, "bob", role="user")
    acc = await api.container.accounts.create(bob_id, "BobBlocked")

    # Владелец блокирует аккаунт
    r = await api.client.patch(
        f"/api/v1/admin/accounts/{acc.id}",
        json={"blocked": True, "reason": "Заблокирован"},
        headers=api.headers,
    )
    assert r.status_code == 200

    # Боб логинится и пытается включить аккаунт
    app = create_api(api.container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as user_client:
        csrf = await login(user_client, login="bob")
        user_headers = {"X-CSRF-Token": csrf}

        # Включение заблокированного аккаунта возвращает 403 blocked_by_owner
        resp = await user_client.patch(
            f"/api/v1/accounts/{acc.id}",
            json={"enabled": True},
            headers=user_headers,
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "blocked_by_owner"

        # Переименование разрешено
        resp_rename = await user_client.patch(
            f"/api/v1/accounts/{acc.id}",
            json={"name": "Новое Имя"},
            headers=user_headers,
        )
        assert resp_rename.status_code == 200
        assert resp_rename.json()["name"] == "Новое Имя"


async def test_user_sees_block_reason_in_account_list(api: Api) -> None:
    bob_id = await make_user(api.container, "bob", role="user")
    acc = await api.container.accounts.create(bob_id, "BobListTest")

    # Блокируем
    await api.client.patch(
        f"/api/v1/admin/accounts/{acc.id}",
        json={"blocked": True, "reason": "Причина блокировки"},
        headers=api.headers,
    )

    # Боб просматривает список аккаунтов
    app = create_api(api.container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as user_client:
        csrf = await login(user_client, login="bob")
        user_headers = {"X-CSRF-Token": csrf}

        resp = await user_client.get("/api/v1/accounts", headers=user_headers)
        assert resp.status_code == 200
        accounts = resp.json()
        item = next((a for a in accounts if a["id"] == acc.id), None)
        assert item is not None
        assert item["blocked"] is True
        assert item["blocked_reason"] == "Причина блокировки"


async def test_restart_and_delete_by_owner(api: Api) -> None:
    bob_id = await make_user(api.container, "bob", role="user")
    acc = await api.container.accounts.create(bob_id, "BobToDelete")

    # 1. Рестарт когда движок НЕ запущен -> 503 engine not running
    r = await api.client.post(
        f"/api/v1/admin/accounts/{acc.id}/restart",
        headers=api.headers,
    )
    assert r.status_code == 503
    assert r.json()["detail"] == "engine not running"

    # 2. Запускаем фейковый движок
    facade = build()
    api.container.engines.put(facade, acc.id)

    # Рестарт когда движок запущен -> 202 Accepted
    pokes_before = api.container.engines.pokes
    r = await api.client.post(
        f"/api/v1/admin/accounts/{acc.id}/restart",
        headers=api.headers,
    )
    assert r.status_code == 202
    assert api.container.engines.pokes > pokes_before

    # 3. Удаление: ошибка имени -> 422 confirm_name_mismatch
    r = await api.client.request(
        "DELETE",
        f"/api/v1/admin/accounts/{acc.id}",
        json={"confirm_name": "WrongName"},
        headers=api.headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "confirm_name_mismatch"

    # 4. Удаление с правильным именем -> 202 Accepted
    pokes_before = api.container.engines.pokes
    r = await api.client.request(
        "DELETE",
        f"/api/v1/admin/accounts/{acc.id}",
        json={"confirm_name": "BobToDelete"},
        headers=api.headers,
    )
    assert r.status_code == 202
    assert api.container.engines.pokes > pokes_before

    # Статус аккаунта стал deleting
    info = await api.container.accounts.get(acc.id)
    assert info is not None
    assert info.status == "deleting"

    # Аудит: account_deleted
    async with api.db.sessions() as s:
        audit = await s.scalar(
            select(AuditRow)
            .where(AuditRow.action == "account_deleted", AuditRow.target_id == acc.id)
            .order_by(AuditRow.id.desc())
        )
        assert audit is not None

    # Повторный restart аккаунта в deleting -> 409 account_deleting
    r = await api.client.post(
        f"/api/v1/admin/accounts/{acc.id}/restart",
        headers=api.headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "account_deleting"

    # Повторное удаление аккаунта в deleting -> 409 account_deleting
    r = await api.client.request(
        "DELETE",
        f"/api/v1/admin/accounts/{acc.id}",
        json={"confirm_name": "BobToDelete"},
        headers=api.headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "account_deleting"


async def test_owner_still_404_on_account_paths(api: Api) -> None:
    # Review Focus 5: владелец сервера (admin) получает 404 на путях чужого
    # аккаунта /api/v1/accounts/{id}/*
    bob_id = await make_user(api.container, "bob", role="user")
    acc = await api.container.accounts.create(bob_id, "BobPrivate")

    # Админ обращается к чужому аккаунту напрямую
    resp = await api.client.get(f"/api/v1/accounts/{acc.id}/settings", headers=api.headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "account not found"

    resp = await api.client.patch(
        f"/api/v1/accounts/{acc.id}",
        json={"name": "Hacked"},
        headers=api.headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "account not found"

    resp = await api.client.post(
        f"/api/v1/accounts/{acc.id}/engine/restart",
        headers=api.headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "account not found"

    resp = await api.client.request(
        "DELETE",
        f"/api/v1/accounts/{acc.id}",
        json={"confirm_name": "BobPrivate"},
        headers=api.headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "account not found"
