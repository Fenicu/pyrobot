import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.app import create_api
from app.api.container import Container
from app.db.base import Database
from app.db.models import AuthSession
from app.engine.transport.fake import FakeTgBackend
from tests.api.conftest import PASSWORD
from tests.engine.test_facade import build
from tools.login import LoginFailed, login_flow

pytestmark = pytest.mark.db
EXPECTED = 267519921
SECRETS = (PASSWORD, "12345", "00000", "2fa-secret", "wrong-2fa")


class Console:
    """Ответы на вопросы скрипта по очереди; всё, что он показал, — в `shown`."""

    def __init__(self, answers: list[str], secrets: list[str]) -> None:
        self.answers = answers
        self.secrets = secrets
        self.shown: list[str] = []

    def ask(self, prompt: str) -> str:
        self.shown.append(prompt)
        return self.answers.pop(0)

    def secret(self, prompt: str) -> str:
        self.shown.append(prompt)
        return self.secrets.pop(0)

    def say(self, text: str) -> None:
        self.shown.append(text)


async def _client(container: Container, backend: FakeTgBackend) -> AsyncClient:
    container.facade = build(authorized=backend.authorized, backend=backend)
    await container.facade.tg.boot()
    return AsyncClient(transport=ASGITransport(app=create_api(container)), base_url="http://t")


async def _open_sessions(db: Database) -> int:
    async with db.sessions() as s:
        return int(await s.scalar(select(func.count()).select_from(AuthSession)) or 0)


async def test_full_login_with_retries_and_2fa(container: Container, clean_db: Database) -> None:
    backend = FakeTgBackend(password="2fa-secret")
    secrets = [PASSWORD, "00000", "12345", "wrong-2fa", "2fa-secret"]
    console = Console(["admin", "+79990000000"], secrets)
    async with await _client(container, backend) as client:
        user = await login_flow(client, console.ask, console.secret, console.say)
    assert user == EXPECTED and backend.online
    assert console.answers == [] and console.secrets == []
    shown = "\n".join(console.shown)
    assert (
        "code rejected: invalid_code" in shown and "password rejected: invalid_password" in shown
    )
    assert not any(s in shown for s in SECRETS)
    # Сессия админа закрыта в конце.
    assert await _open_sessions(clean_db) == 0


async def test_already_online_needs_only_admin(container: Container, clean_db: Database) -> None:
    console = Console([""], [PASSWORD])
    async with await _client(container, FakeTgBackend(authorized=True)) as client:
        assert await login_flow(client, console.ask, console.secret, console.say) == EXPECTED
    assert "telegram is already online" in console.shown


async def test_unexpected_user_fails(container: Container, clean_db: Database) -> None:
    console = Console(["admin", "+79990000000"], [PASSWORD, "12345"])
    async with await _client(container, FakeTgBackend(user_id=42)) as client:
        with pytest.raises(LoginFailed, match="unexpected_user"):
            await login_flow(client, console.ask, console.secret, console.say)
    assert await _open_sessions(clean_db) == 0


async def test_wrong_admin_password_fails(container: Container, clean_db: Database) -> None:
    console = Console(["admin"], ["not the password"])
    async with await _client(container, FakeTgBackend()) as client:
        with pytest.raises(LoginFailed, match="HTTP 401"):
            await login_flow(client, console.ask, console.secret, console.say)
