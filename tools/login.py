"""Первый вход без админки: вход админа, затем вход в Telegram через API сервиса.

    uv run python tools/login.py https://sw.fenicu.com

Спрашивает логин и пароль админа, телефон, код из Telegram и пароль 2FA. Коды и пароли вводятся
без эха, не печатаются и не логируются; cookie сессии и CSRF-токен живут только в памяти процесса
(cookie передаётся заголовком — так вход работает и напрямую по http внутри сети, где браузер не
отправил бы Secure-cookie). В конце user_id вошедшего аккаунта сверяется с
telegram.expected_user_id из настроек сервиса, сессия админа закрывается."""

import asyncio
import getpass
import sys
from collections.abc import Callable
from typing import Any

import httpx

COOKIE = "pyrobot_session"
ATTEMPTS = 3
Ask = Callable[[str], str]
Say = Callable[[str], None]


class LoginFailed(Exception):
    pass


class _Session:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._cookie: str | None = None
        self._csrf: str | None = None

    async def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        headers = {}
        if self._cookie is not None:
            headers["Cookie"] = f"{COOKIE}={self._cookie}"
        if self._csrf is not None and method != "GET":
            headers["X-CSRF-Token"] = self._csrf
        resp = await self._client.request(method, path, json=body, headers=headers)
        # Хранилище cookie клиента не используется: сессия — только в этом объекте.
        self._client.cookies.clear()
        if resp.status_code == 204:
            return None
        if resp.status_code != 200:
            detail = resp.json().get("detail") if resp.content else None
            raise LoginFailed(f"{method} {path}: HTTP {resp.status_code} {detail or ''}".strip())
        return resp.json()

    async def login(self, login: str, password: str) -> None:
        resp = await self._client.post(
            "/api/v1/auth/login", json={"login": login, "password": password}
        )
        self._client.cookies.clear()
        if resp.status_code != 200:
            raise LoginFailed(f"admin login failed: HTTP {resp.status_code}")
        self._csrf = resp.json()["csrf_token"]
        for header in resp.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            if name.strip() == COOKIE:
                self._cookie = rest.split(";", 1)[0]
        if self._cookie is None:
            raise LoginFailed("admin login returned no session cookie")

    async def logout(self) -> None:
        if self._cookie is not None:
            await self.call("POST", "/api/v1/auth/logout")
            self._cookie = self._csrf = None


async def _telegram(api: _Session, ask: Ask, secret: Ask, say: Say) -> dict[str, Any]:
    status: dict[str, Any] = await api.call("GET", "/api/v1/tg/status")
    if status["state"] == "online":
        say("telegram is already online")
        return status
    phone = ask("phone (+7…): ").strip()
    status = await api.call("POST", "/api/v1/tg/login/start", {"phone": phone})
    attempt = status["attempt_id"]
    for _ in range(ATTEMPTS):
        if status["state"] != "awaiting_code":
            break
        code = secret("code from Telegram: ").strip()
        status = await api.call(
            "POST", "/api/v1/tg/login/code", {"attempt_id": attempt, "code": code}
        )
        if status["state"] == "awaiting_code":
            say(f"code rejected: {status['error']}")
    for _ in range(ATTEMPTS):
        if status["state"] != "awaiting_password":
            break
        password = secret("2FA password: ")
        status = await api.call(
            "POST", "/api/v1/tg/login/password", {"attempt_id": attempt, "password": password}
        )
        if status["state"] == "awaiting_password" and status["error"]:
            say(f"password rejected: {status['error']}")
    return status


async def login_flow(client: httpx.AsyncClient, ask: Ask, secret: Ask, say: Say) -> int:
    """Весь вход; возвращает user_id аккаунта Telegram, LoginFailed — вход не удался."""
    api = _Session(client)
    await api.login(ask("admin login [admin]: ").strip() or "admin", secret("admin password: "))
    try:
        settings = await api.call("GET", "/api/v1/settings")
        expected = int(settings["values"]["telegram"]["expected_user_id"])
        status = await _telegram(api, ask, secret, say)
        if status["state"] != "online":
            raise LoginFailed(f"telegram: {status['state']} {status['error'] or ''}".strip())
        if status["user_id"] != expected:
            raise LoginFailed(f"telegram user {status['user_id']} is not {expected}")
        say(f"telegram online as {status['user_id']} (expected_user_id matches)")
        return expected
    finally:
        await api.logout()


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: tools/login.py <service url>", file=sys.stderr)
        sys.exit(2)

    async def run() -> None:
        async with httpx.AsyncClient(base_url=sys.argv[1], timeout=60) as client:
            await login_flow(client, input, getpass.getpass, print)

    try:
        asyncio.run(run())
    except LoginFailed as exc:
        print(f"login failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
