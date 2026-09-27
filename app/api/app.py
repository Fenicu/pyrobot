from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.container import Container
from app.api.routes_auth import router as auth_router
from app.api.routes_engine import router as engine_router
from app.api.routes_settings import router as settings_router
from app.api.routes_state import router as state_router


def create_api(container: Container) -> FastAPI:
    app = FastAPI(
        title="pyrobot", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.container = container
    app.include_router(auth_router)
    app.include_router(engine_router)
    app.include_router(state_router)
    app.include_router(settings_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        f = container.facade
        if f is not None and f.ready():
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "not_ready"}, status_code=503)

    return app
