"""Проверка живости для HEALTHCHECK образа и ожидания готовности при деплое (без curl в образе):
`python -m app.healthcheck [/healthz|/readyz]`, код выхода 0 — ответ 200."""

import os
import sys
import urllib.error
import urllib.request


def check(path: str = "/healthz", port: str | None = None, timeout_s: float = 3.0) -> bool:
    port = port or os.environ.get("PYROBOT_HTTP_PORT", "8080")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout_s) as resp:
            return bool(resp.status == 200)
    except urllib.error.HTTPError as exc:
        # Ответ не 2xx приходит исключением с открытым соединением — закрыть его самим.
        exc.close()
        return False
    except (urllib.error.URLError, OSError):
        return False


if __name__ == "__main__":
    sys.exit(0 if check(sys.argv[1] if len(sys.argv) > 1 else "/healthz") else 1)
