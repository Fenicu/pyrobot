"""Отдача собранной админки (SvelteKit SPA) тем же приложением, что и API.

Один маршрут в конце списка: существующий файл каталога — файл, отсутствующий файл с расширением —
404, путь без расширения (навигация SPA) — `index.html`. Пути `/api`, `/healthz`, `/readyz` маршрут
не обслуживает: у них прежние 404 и 405."""

import base64
import hashlib
import logging
import re
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from starlette.routing import Match, Route
from starlette.types import Scope

log = logging.getLogger(__name__)
INDEX = "index.html"
IMMUTABLE = "_app/immutable/"
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
_RESERVED = ("/api", "/healthz", "/readyz")
# Встроенный скрипт — `<script>` без `src`: его содержимое хешируется для CSP как есть.
_INLINE_SCRIPT = re.compile(
    r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL
)


def inline_script_hashes(html: str) -> list[str]:
    """`'sha256-…'` каждого встроенного скрипта страницы в порядке появления."""
    hashes = []
    for body in _INLINE_SCRIPT.findall(html):
        digest = hashlib.sha256(body.encode()).digest()
        hashes.append(f"'sha256-{base64.b64encode(digest).decode()}'")
    return hashes


def content_security_policy(html: str) -> str:
    scripts = " ".join(["'self'", *inline_script_hashes(html)])
    return "; ".join(
        (
            "default-src 'self'",
            f"script-src {scripts}",
            "img-src 'self' data:",
            "style-src 'self' 'unsafe-inline'",
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        )
    )


def _reserved(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _RESERVED)


class AdminStatic:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        # index.html читается один раз: отдаётся ровно тот файл, по которому посчитан CSP.
        self.index = (self.root / INDEX).read_bytes()
        self.headers = {
            "Content-Security-Policy": content_security_policy(self.index.decode()),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
        }

    def respond(self, path: str) -> Response:
        rel = PurePosixPath(path.lstrip("/"))
        if str(rel) in ("", ".", INDEX):
            return self._index()
        target = (self.root / rel).resolve()
        if target.is_relative_to(self.root) and target.is_file():
            cache = IMMUTABLE_CACHE if str(rel).startswith(IMMUTABLE) else "no-cache"
            return FileResponse(target, headers={**self.headers, "Cache-Control": cache})
        if rel.suffix:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return self._index()

    def _index(self) -> Response:
        return Response(
            self.index,
            media_type="text/html; charset=utf-8",
            headers={**self.headers, "Cache-Control": "no-cache"},
        )


class _AdminRoute(Route):
    """Маршрут админки не совпадает с путями API и проб: для них остаются прежние ответы (404
    неизвестного пути, 405 чужого метода), а не index.html."""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope["type"] != "http" or _reserved(scope["path"]):
            return Match.NONE, {}
        if scope["method"] not in ("GET", "HEAD"):
            return Match.NONE, {}
        return super().matches(scope)


def install_admin(app: FastAPI, root: Path | None) -> AdminStatic | None:
    """Подключает отдачу админки последним маршрутом; нет каталога или `index.html` — ничего."""
    if root is None or not (root / INDEX).is_file():
        log.info("admin build not found at %s, / is not served", root)
        return None
    static = AdminStatic(root)

    async def admin(request: Request) -> Response:
        return static.respond(request.path_params["path"])

    app.router.routes.append(
        _AdminRoute("/{path:path}", admin, methods=["GET", "HEAD"], include_in_schema=False)
    )
    return static
