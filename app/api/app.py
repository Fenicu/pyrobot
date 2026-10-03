from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.admin_static import install_admin
from app.api.container import Container
from app.api.errors import CHAT_IS_SELF, ENGINE_NOT_RUNNING
from app.api.routes_accounts import account as account_router
from app.api.routes_accounts import router as accounts_router
from app.api.routes_artifact import router as artifact_router
from app.api.routes_auth import router as auth_router
from app.api.routes_commands import catalog_router
from app.api.routes_commands import router as commands_router
from app.api.routes_daily import router as daily_router
from app.api.routes_engine import router as engine_router
from app.api.routes_events import router as events_router
from app.api.routes_journal import router as journal_router
from app.api.routes_planner import router as planner_router
from app.api.routes_reference import router as reference_router
from app.api.routes_settings import router as settings_router
from app.api.routes_state import router as state_router
from app.db.base import Database
from app.engine.fence import LeaseLost
from app.engine.settings import ChatIsSelf


async def _lease_lost(_: Request, __: Exception) -> JSONResponse:
    # Аренда аккаунта потеряна посреди вызова движка: хост остановит его и захватит снова —
    # для клиента это движок, который сейчас не запущен.
    return JSONResponse({"detail": ENGINE_NOT_RUNNING}, status_code=503)


async def _chat_is_self(_: Request, exc: Exception) -> JSONResponse:
    # Правка настроек привязанного аккаунта с его же чатом (раздел 4.3 спеки): поля — рядом.
    assert isinstance(exc, ChatIsSelf)
    return JSONResponse({"detail": CHAT_IS_SELF, "fields": exc.fields}, status_code=422)


async def _db_ok(db: Database) -> bool:
    try:
        async with db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


def create_api(container: Container) -> FastAPI:
    app = FastAPI(
        title="pyrobot", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.container = container
    app.add_exception_handler(LeaseLost, _lease_lost)
    app.add_exception_handler(ChatIsSelf, _chat_is_self)
    app.include_router(auth_router)
    app.include_router(accounts_router)
    app.include_router(account_router)
    app.include_router(catalog_router)
    app.include_router(engine_router)
    app.include_router(planner_router)
    app.include_router(artifact_router)
    app.include_router(state_router)
    app.include_router(settings_router)
    app.include_router(journal_router)
    app.include_router(commands_router)
    app.include_router(reference_router)
    app.include_router(daily_router)
    app.include_router(events_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        # Готовность процесса: соединение блокировок хоста живо и база отвечает. Готовность
        # аккаунта (Telegram онлайн, не kill) — в его статусе.
        if container.engines.status().lock_connection_ok and await _db_ok(container.db):
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "not_ready"}, status_code=503)

    # Последним: маршрут админки забирает все прочие GET-пути, кроме /api и проб.
    install_admin(app, container.config.admin_dir)
    return app
