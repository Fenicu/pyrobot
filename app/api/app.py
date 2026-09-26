from fastapi import FastAPI

from app.api.container import Container
from app.api.routes_auth import router as auth_router


def create_api(container: Container) -> FastAPI:
    app = FastAPI(title="pyrobot", version="0.1.0")
    app.state.container = container
    app.include_router(auth_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
