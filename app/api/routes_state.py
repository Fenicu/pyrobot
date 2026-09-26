from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import SessionContext, current_session
from app.api.routes_engine import facade
from app.engine.facade import EngineFacade
from app.engine.state.model import load_state, stale_fields

router = APIRouter(prefix="/api/v1", tags=["state"])


@router.get("/state")
async def get_state(
    f: Annotated[EngineFacade, Depends(facade)],
    _: Annotated[SessionContext, Depends(current_session)],
) -> dict[str, Any]:
    version, snapshot = f.state()
    now = datetime.now(UTC)
    max_age = timedelta(minutes=f.settings.current.engine.state_stale_after_min)
    return {
        "version": version,
        "now": now.isoformat(),
        "state": snapshot,
        "stale": stale_fields(load_state(snapshot), now, max_age),
    }
