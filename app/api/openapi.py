from typing import Any

from app.api.app import create_api
from app.api.container import Container
from app.api.security import LoginRateLimiter
from app.config import AppConfig
from app.db.auth_repo import AuthRepo
from app.db.base import Database
from app.db.reads import DbReads


def build_schema() -> dict[str, Any]:
    """OpenAPI без запуска сервиса и БД (движок ленивый, соединений не открывает): в самом
    сервисе `/openapi.json` выключен, схема нужна для TS-типов админки."""
    db = Database(AppConfig.model_fields["database_url"].default)
    container = Container(
        config=AppConfig(_env_file=None, transport="fake"),  # type: ignore[call-arg]
        auth=AuthRepo(db),
        limiter=LoginRateLimiter(),
        reads=DbReads(db, 1),
    )
    return create_api(container).openapi()
