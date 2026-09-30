"""Отдача админки из каталога сборки: файлы, SPA-fallback, кеш, CSP и нетронутые /api и пробы."""

import base64
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.admin_static import content_security_policy, inline_script_hashes
from app.api.app import create_api
from app.api.container import Container
from app.api.security import LoginRateLimiter
from app.config import AppConfig
from app.db.accounts import AccountRepo
from app.db.auth_repo import AuthRepo
from app.db.base import Database
from tests.api.conftest import FakeEngines

# Форма стартовой страницы adapter-static (fallback SPA): встроенный стартовый скрипт.
BOOT = """
				{
					__sveltekit_x1 = { base: "" };
					const element = document.currentScript.parentElement;
					Promise.all([
						import("/_app/immutable/entry/start.B1.js"),
						import("/_app/immutable/entry/app.C2.js")
					]).then(([kit, app]) => { kit.start(app, element); });
				}
			"""
INDEX = f"""<!doctype html>
<html lang="ru">
	<head>
		<meta charset="utf-8" />
		<link rel="modulepreload" href="/_app/immutable/entry/start.B1.js">
		<script type="module" src="/_app/immutable/entry/extra.js"></script>
	</head>
	<body>
		<div style="display: contents">
			<script>{BOOT}</script>
			<SCRIPT data-x="1">console.log("два")</SCRIPT>
		</div>
	</body>
</html>
"""


def _sha(body: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"


@pytest.fixture
def build_dir(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text(INDEX, encoding="utf-8")
    immutable = tmp_path / "_app" / "immutable" / "entry"
    immutable.mkdir(parents=True)
    (immutable / "start.B1.js").write_text("export const start = 1;\n", encoding="utf-8")
    (tmp_path / "_app" / "version.json").write_text('{"version":"1"}', encoding="utf-8")
    (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return tmp_path


def _app(admin_dir: Path | None) -> AsyncClient:
    db = Database(AppConfig.model_fields["database_url"].default)
    cfg = AppConfig(_env_file=None, transport="fake", admin_dir=admin_dir)  # type: ignore[call-arg]
    container = Container(
        config=cfg,
        auth=AuthRepo(db),
        limiter=LoginRateLimiter(),
        db=db,
        accounts=AccountRepo(db),
        engines=FakeEngines(),
    )
    return AsyncClient(transport=ASGITransport(app=create_api(container)), base_url="http://t")


@pytest.fixture
async def client(build_dir: Path) -> AsyncIterator[AsyncClient]:
    async with _app(build_dir) as c:
        yield c


def test_csp_hashes_match_inline_scripts() -> None:
    assert inline_script_hashes(INDEX) == [_sha(BOOT), _sha('console.log("два")')]
    csp = content_security_policy(INDEX)
    assert csp == (
        f"default-src 'self'; script-src 'self' {_sha(BOOT)} {_sha('console.log("два")')}; "
        "img-src 'self' data:; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )


async def test_index_and_spa_fallback(client: AsyncClient) -> None:
    for path in ("/", "/index.html", "/journal", "/settings/engine", "/metro/1", "/a%00b"):
        r = await client.get(path)
        assert r.status_code == 200, path
        assert r.text == INDEX
        assert r.headers["content-type"] == "text/html; charset=utf-8"
        assert r.headers["cache-control"] == "no-cache"
        assert r.headers["content-security-policy"] == content_security_policy(INDEX)
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "same-origin"


async def test_files_and_cache(client: AsyncClient) -> None:
    js = await client.get("/_app/immutable/entry/start.B1.js")
    assert js.status_code == 200 and js.text == "export const start = 1;\n"
    assert js.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "javascript" in js.headers["content-type"]
    assert js.headers["x-content-type-options"] == "nosniff"
    version = await client.get("/_app/version.json")
    assert version.status_code == 200 and version.headers["cache-control"] == "no-cache"
    icon = await client.get("/favicon.svg")
    assert icon.status_code == 200 and icon.headers["cache-control"] == "no-cache"
    head = await client.head("/_app/immutable/entry/start.B1.js")
    assert head.status_code == 200


async def test_missing_file_with_extension_is_404(client: AsyncClient) -> None:
    for path in (
        "/_app/immutable/x.js",
        "/robots.txt",
        "/../config.py",
        "/_app/%2e%2e/secret.js",
        "/a%00b.js",
    ):
        r = await client.get(path)
        assert r.status_code == 404, path
        assert r.json() == {"detail": "Not Found"}


async def test_api_and_probes_not_intercepted(client: AsyncClient) -> None:
    for path in ("/api", "/api/", "/api/v1/nope", "/api/v2/state"):
        r = await client.get(path)
        assert (r.status_code, r.json()) == (404, {"detail": "Not Found"}), path
    # Известный путь API с чужим методом — прежний 405, а не index.html.
    assert (await client.get("/api/v1/auth/login")).status_code == 405
    assert (await client.get("/api/v1/accounts/1/state")).status_code == 401
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = await client.get("/readyz")
    assert (ready.status_code, ready.json()) == (503, {"status": "not_ready"})
    assert (await client.post("/healthz")).status_code == 405
    # Изменяющие методы на пути SPA — прежний 404.
    assert (await client.post("/journal")).status_code == 404


@pytest.mark.parametrize("admin_dir", [None, "missing", "empty"])
async def test_without_build_root_is_404(tmp_path: Path, admin_dir: str | None) -> None:
    root = None if admin_dir is None else tmp_path / admin_dir
    if admin_dir == "empty":
        assert root is not None
        root.mkdir()
    async with _app(root) as c:
        assert (await c.get("/")).status_code == 404
        assert (await c.get("/journal")).status_code == 404
        assert (await c.get("/healthz")).status_code == 200


def test_real_build_index_hashes() -> None:
    # Стартовая страница настоящей сборки админки (`npm run build`, adapter-static): скрипт темы
    # в <head> до отрисовки и стартовый скрипт SvelteKit — оба разрешены CSP своими хешами.
    html = (Path(__file__).parent.parent / "fixtures" / "admin" / "index.html").read_text(
        encoding="utf-8"
    )
    bodies, at = [], 0
    while (start := html.find("<script>", at)) != -1:
        start += len("<script>")
        at = html.index("</script>", start)
        bodies.append(html[start:at])
    assert len(bodies) == 2
    assert "pyrobot.theme" in bodies[0] and html.index("pyrobot.theme") < html.index("</head>")
    assert "__sveltekit_" in bodies[1]
    assert inline_script_hashes(html) == [_sha(b) for b in bodies]
    scripts = content_security_policy(html).split("; ")[1]
    assert scripts == " ".join(["script-src 'self'", *(_sha(b) for b in bodies)])
