from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.errors import AUTH
from app.api.scope import AccountScope, account_router, account_scope
from app.api.state_schema import PublicState
from app.engine.state.model import load_state, stale_fields

router = account_router("state")
# Наружу — только поля публичной схемы: служебное `applied` редьюсера и поля снимка прошлой
# сборки, которых уже нет в модели, в ответ не входят.
_PUBLIC = frozenset(PublicState.model_fields)


class StateOut(BaseModel):
    version: int
    now: str = Field(json_schema_extra={"format": "date-time"})
    # Снимок как есть: поле — `{value, at, src}` или null (ещё не наблюдалось); до первого
    # сообщения — пустой объект.
    state: PublicState
    # Устаревшие поля по политике свежести (`prices.<ключ>` — цены).
    stale: list[str]


@router.get("/state", response_model=StateOut, responses=AUTH)
async def get_state(scope: Annotated[AccountScope, Depends(account_scope)]) -> JSONResponse:
    """Состояние запущенного движка; без движка — последний сохранённый снимок."""
    f = scope.facade
    if f is not None:
        version, snapshot = f.state()
        settings = f.settings.current
    else:
        version, snapshot = await scope.reads.state()
        settings, _ = await scope.reads.settings()
    now = datetime.now(UTC)
    max_age = timedelta(minutes=settings.engine.state_stale_after_min)
    # Снимок уходит без пересборки моделью: порядок, даты и поля — как в конвейере.
    return JSONResponse(
        {
            "version": version,
            "now": now.isoformat(),
            "state": {k: v for k, v in snapshot.items() if k in _PUBLIC},
            "stale": stale_fields(load_state(snapshot), now, max_age),
        }
    )
