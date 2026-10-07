"""Ответы с ошибками в OpenAPI. Форма — как у `HTTPException` FastAPI: `{"detail": …}`;
модели только описывают её для TS-типов админки, ответы через них не проходят."""

from typing import Any, Final, Literal

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


class ValidationIssueOut(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ValidationErrorOut(BaseModel):
    """Значения не прошли проверку: ошибка с путём на каждое поле."""

    detail: list[ValidationIssueOut]


class ChatIsSelfOut(BaseModel):
    """Поля `chats.*` равны пользователю Telegram, к которому привязан аккаунт: его «Избранное»
    не попадает в журнал."""

    detail: Literal["chat_is_self"]
    fields: list[str]


class OutOfBoundsOut(BaseModel):
    """Значение настройки движка выходит за границы, заданные настройками сервера."""

    detail: Literal["setting_out_of_bounds"]
    path: str
    bound: Literal["min", "max"]
    limit: float


class AccountErrorOut(BaseModel):
    """Ошибка про один из аккаунтов запроса: какой — `account_id`."""

    detail: str
    account_id: int


class TangerinePairPartialOut(BaseModel):
    """Пара для мандаринов не собрана: сообщение аккаунта уже в чате, у партнёра — нет."""

    detail: Literal["tangerine_pair_partial"]
    # Код ошибки партнёра, как у одиночного сообщения (`tg_not_online`, `flood_wait`, …).
    reason: str
    # Отправленные сообщения: id аккаунта → id сообщения.
    posted: dict[int, int]
    failed: int


def error(*codes: str) -> dict[str, Any]:
    """Описание ответа `{"detail": "<код>"}` с перечнем возможных кодов."""
    return {"model": ErrorOut, "description": " | ".join(codes)}


NOT_AUTHENTICATED = "not authenticated"
CSRF_MISMATCH = "csrf token mismatch"
# Чужой и несуществующий аккаунт неразличимы.
ACCOUNT_NOT_FOUND = "account not found"
ENGINE_NOT_RUNNING = "engine not running"
# Аренда аккаунта занята, а его движок ещё не зарегистрирован в хосте: повторить позже.
ENGINE_STARTING = "engine_starting"
ACCOUNT_DELETING = "account_deleting"
NAME_TAKEN = "name_taken"
CAPACITY_REACHED = "capacity_reached"
LIMIT_REACHED = "limit_reached"
SERVER_FULL = "server_full"
TOO_MANY_STREAMS = "too_many_streams"
CONFIRM_NAME_MISMATCH = "confirm_name_mismatch"
CHAT_IS_SELF = "chat_is_self"
SETTING_OUT_OF_BOUNDS = "setting_out_of_bounds"
# Запросов кода входа в Telegram больше лимита хоста или аккаунта (с `Retry-After`).
TG_CODE_RATE_LIMITED = "tg_code_rate_limited"
FLOOD_WAIT = "flood_wait"
TG_NOT_ONLINE = "tg_not_online"
TG_LOGGED_IN = "tg_logged_in"
# Чат по username общего чата игры — не `chats.swinfo_chat_id`: аккаунт в него не вступает.
GAME_CHAT_MISMATCH = "game_chat_mismatch"
# Чат по username чата мандаринов — не `chats.tangerine_chat_id`: аккаунт в него не вступает.
TANGERINE_CHAT_MISMATCH = "tangerine_chat_mismatch"
TANGERINE_PAIR_SELF = "tangerine_pair_self"
TANGERINE_PAIR_PARTIAL: Final = "tangerine_pair_partial"
INVITE_NOT_FOUND = "invite_not_found"
INVITE_GONE = "invite_gone"
LOGIN_TAKEN = "login_taken"
LAST_OWNER = "last_owner"
USER_NOT_FOUND = "user_not_found"
CONFIRM_LOGIN_MISMATCH = "confirm_login_mismatch"
BLOCKED_BY_OWNER = "blocked_by_owner"
REASON_REQUIRED = "reason_required"
INVALID_PASSWORD = "invalid_password"
INVALID_CODE = "invalid_code"
# Сессия (cookie): без неё или с истёкшей — 401.
AUTH: Responses = {401: error(NOT_AUTHENTICATED)}
# Изменяющий запрос: сессия и заголовок X-CSRF-Token.
CSRF: Responses = {**AUTH, 403: error(CSRF_MISMATCH)}
# Эндпоинты движка: без запущенного движка аккаунта — 503.
ENGINE: Responses = {503: error(ENGINE_NOT_RUNNING)}


def not_found(what: str) -> Responses:
    """404 пути аккаунта со своим объектом: аккаунта нет или нет объекта."""
    return {404: error(ACCOUNT_NOT_FOUND, f"{what} not found")}
