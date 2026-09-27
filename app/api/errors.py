"""Ответы с ошибками в OpenAPI. Форма — как у `HTTPException` FastAPI: `{"detail": …}`;
модели только описывают её для TS-типов админки, ответы через них не проходят."""

from typing import Any, Literal

from pydantic import BaseModel

Responses = dict[int | str, dict[str, Any]]


class ErrorOut(BaseModel):
    """Ошибка со строковым кодом."""

    detail: str


class VersionConflict(BaseModel):
    code: Literal["version_conflict"]
    # Текущая версия настроек: клиент перечитывает их и повторяет изменение.
    version: int


class VersionConflictOut(BaseModel):
    detail: VersionConflict


def error(*codes: str) -> dict[str, Any]:
    """Описание ответа `{"detail": "<код>"}` с перечнем возможных кодов."""
    return {"model": ErrorOut, "description": " | ".join(codes)}


def not_found(what: str) -> Responses:
    return {404: error(f"{what} not found")}


NOT_AUTHENTICATED = "not authenticated"
CSRF_MISMATCH = "csrf token mismatch"
ENGINE_NOT_STARTED = "engine not started"
# Сессия (cookie): без неё или с истёкшей — 401.
AUTH: Responses = {401: error(NOT_AUTHENTICATED)}
# Изменяющий запрос: сессия и заголовок X-CSRF-Token.
CSRF: Responses = {**AUTH, 403: error(CSRF_MISMATCH)}
# Эндпоинты движка: без запущенного движка — 503.
ENGINE: Responses = {503: error(ENGINE_NOT_STARTED)}
