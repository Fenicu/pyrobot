import httpx
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


async def _client(
    container: Container, backend: FakeTgBackend, bound: int | None = None
) -> AsyncClient:
    container.facade = build(authorized=backend.authorized, backend=backend, bound_user_id=bound)
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
    assert f"telegram online as {EXPECTED} (account bound on first login)" in shown
    # Сессия админа закрыта в конце.
    assert await _open_sessions(clean_db) == 0


async def test_already_online_needs_only_admin(container: Container, clean_db: Database) -> None:
    console = Console([""], [PASSWORD])
    async with await _client(container, FakeTgBackend(authorized=True), EXPECTED) as client:
        assert await login_flow(client, console.ask, console.secret, console.say) == EXPECTED
    assert "telegram is already online" in console.shown
    assert f"telegram online as {EXPECTED} (matches the bound user)" in console.shown
    assert await _open_sessions(clean_db) == 0


async def test_unexpected_user_fails(container: Container, clean_db: Database) -> None:
    console = Console(["admin", "+79990000000"], [PASSWORD, "12345"])
    async with await _client(container, FakeTgBackend(user_id=42), EXPECTED) as client:
        with pytest.raises(LoginFailed, match="unexpected_user"):
            await login_flow(client, console.ask, console.secret, console.say)
    assert await _open_sessions(clean_db) == 0


async def test_wrong_admin_password_fails(container: Container, clean_db: Database) -> None:
    console = Console(["admin"], ["not the password"])
    async with await _client(container, FakeTgBackend()) as client:
        with pytest.raises(LoginFailed, match="HTTP 401"):
            await login_flow(client, console.ask, console.secret, console.say)


HTML = "<html><body><h1>502 Bad Gateway</h1>upstream admin:hunter2</body></html>"
LOGGED_IN = {"login": "admin", "csrf_token": "csrf"}


def _proxy(routes: dict[str, httpx.Response]) -> AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(request.url.path, httpx.Response(204))

    return AsyncClient(transport=httpx.MockTransport(handler), base_url="http://t")


def _logged_in() -> httpx.Response:
    return httpx.Response(200, json=LOGGED_IN, headers={"set-cookie": "pyrobot_session=s; Path=/"})


def _html(code: int) -> httpx.Response:
    return httpx.Response(code, text=HTML, headers={"content-type": "text/html"})


async def _fail(routes: dict[str, httpx.Response]) -> str:
    console = Console([""], [PASSWORD])
    async with _proxy(routes) as client:
        with pytest.raises(LoginFailed) as err:
            await login_flow(client, console.ask, console.secret, console.say)
    return str(err.value)


async def test_html_error_from_proxy_is_reported_by_code() -> None:
    routes = {
        "/api/v1/auth/login": _logged_in(),
        "/api/v1/tg/status": _html(502),
        "/api/v1/auth/logout": _html(502),
    }
    # Первопричина не подменяется ошибкой закрытия сессии.
    assert await _fail(routes) == "GET /api/v1/tg/status: HTTP 502"
    assert await _fail({"/api/v1/auth/login": _html(200)}) == (
        "admin login: HTTP 200, not a JSON response"
    )


async def test_error_detail_shown_only_as_code() -> None:
    # Ошибка валидации FastAPI повторяет присланные значения — их не печатаем.
    echoed = httpx.Response(422, json={"detail": [{"msg": "bad", "input": "12345"}]})
    message = await _fail({"/api/v1/auth/login": _logged_in(), "/api/v1/tg/status": echoed})
    assert message == "GET /api/v1/tg/status: HTTP 422"
    coded = httpx.Response(503, json={"detail": "engine not started"})
    message = await _fail({"/api/v1/auth/login": _logged_in(), "/api/v1/tg/status": coded})
    assert message == "GET /api/v1/tg/status: HTTP 503 engine not started"


async def test_user_other_than_bound_fails() -> None:
    # Привязка — из `tg/status.bound_user_id`: онлайн под другим пользователем скрипт не принимает.
    online = httpx.Response(
        200, json={"state": "online", "user_id": 5, "bound_user_id": 42, "error": None}
    )
    assert await _fail({"/api/v1/auth/login": _logged_in(), "/api/v1/tg/status": online}) == (
        "telegram user 5 is not 42"
    )
