from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.deps import SessionContext, current_session
from app.api.errors import AUTH, ENGINE
from app.api.routes_engine import facade
from app.api.state_schema import PublicState
from app.engine.facade import EngineFacade
from app.engine.state.model import load_state, stale_fields

router = APIRouter(prefix="/api/v1", tags=["state"])
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


@router.get("/state", response_model=StateOut, responses={**AUTH, **ENGINE})
async def get_state(
    _: Annotated[SessionContext, Depends(current_session)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> JSONResponse:
    version, snapshot = f.state()
    now = datetime.now(UTC)
    max_age = timedelta(minutes=f.settings.current.engine.state_stale_after_min)
    # Снимок уходит без пересборки моделью: порядок, даты и поля — как в конвейере.
    return JSONResponse(
        {
            "version": version,
            "now": now.isoformat(),
            "state": {k: v for k, v in snapshot.items() if k in _PUBLIC},
            "stale": stale_fields(load_state(snapshot), now, max_age),
        }
    )
