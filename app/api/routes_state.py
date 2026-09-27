from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import SessionContext, current_session
from app.api.routes_engine import facade
from app.engine.facade import EngineFacade
from app.engine.state.model import load_state, stale_fields

router = APIRouter(prefix="/api/v1", tags=["state"])
# Служебные поля редьюсера в ответ не входят.
_INTERNAL = frozenset({"applied"})


class StateOut(BaseModel):
    version: int
    now: str = Field(json_schema_extra={"format": "date-time"})
    # Снимок как есть: поле — `{value, at, src}` или null (ещё не наблюдалось); до первого
    # сообщения — пустой объект.
    state: dict[str, Any]
    # Устаревшие поля по политике свежести (`prices.<ключ>` — цены).
    stale: list[str]


@router.get("/state", response_model=StateOut)
async def get_state(
    _: Annotated[SessionContext, Depends(current_session)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> StateOut:
    version, snapshot = f.state()
    now = datetime.now(UTC)
    max_age = timedelta(minutes=f.settings.current.engine.state_stale_after_min)
    return StateOut(
        version=version,
        now=now.isoformat(),
        state={k: v for k, v in snapshot.items() if k not in _INTERNAL},
        stale=stale_fields(load_state(snapshot), now, max_age),
    )
