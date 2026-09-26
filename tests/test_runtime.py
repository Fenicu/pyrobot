import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import AppConfig
from app.db.base import Database
from app.main import create_application
from tests.conftest import TEST_DB_URL

pytestmark = pytest.mark.db


def _cfg() -> AppConfig:
    return AppConfig(
        _env_file=None,
        database_url=TEST_DB_URL,
        transport="fake",
        cookie_secure=False,
        admin_login="admin",
        admin_password=SecretStr("correct horse battery"),
    )


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


async def test_second_runtime_does_not_start_engine(clean_db: Database) -> None:
    first = create_application(_cfg())
    second = create_application(_cfg())
    async with first.router.lifespan_context(first), second.router.lifespan_context(second):
        async with AsyncClient(transport=ASGITransport(app=second), base_url="http://t") as client:
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.get("/readyz")).status_code == 503
