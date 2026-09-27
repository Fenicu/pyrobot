from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ValidationError

from app.api.container import Container
from app.api.deps import SessionContext, container, current_session, require_csrf
from app.api.routes_engine import facade
from app.engine.facade import EngineFacade
from app.engine.settings import (
    Settings,
    SettingsConflict,
    SettingsPatchError,
    restart_required,
)

router = APIRouter(prefix="/api/v1", tags=["settings"])


class SettingsOut(BaseModel):
    version: int
    values: dict[str, Any]
    defaults: dict[str, Any]
    json_schema: dict[str, Any] = Field(serialization_alias="schema")


class SettingsPatchIn(BaseModel):
    version: int = Field(ge=0)
    changes: dict[str, Any]
    confirm_live: bool = False


class SettingsPatchOut(BaseModel):
    version: int
    values: dict[str, Any]
    changed: dict[str, list[Any]]
    restart_required: list[str]


class SettingsVersionOut(BaseModel):
    version: int
    changed_by: str
    changed_at: datetime
    changes: dict[str, list[Any]]


class SettingsHistoryOut(BaseModel):
    items: list[SettingsVersionOut]
    next_before: int | None


def _unprocessable(errors: list[dict[str, Any]]) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, errors)


@router.get("/settings", response_model=SettingsOut)
async def get_settings(
    _: Annotated[SessionContext, Depends(current_session)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> SettingsOut:
    return SettingsOut(
        version=f.settings.version,
        values=f.settings.current.model_dump(mode="json"),
        defaults=Settings().model_dump(mode="json"),
        json_schema=Settings.model_json_schema(),
    )


@router.patch("/settings", response_model=SettingsPatchOut)
async def patch_settings(
    body: SettingsPatchIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> SettingsPatchOut:
    try:
        upd = await f.patch_settings(
            body.changes, version=body.version, by=ctx.login, confirm_live=body.confirm_live
        )
    except SettingsConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "version_conflict", "version": f.settings.version}
        ) from exc
    except SettingsPatchError as exc:
        loc = ["body", "confirm_live"] if exc.code == "live_requires_confirm" else None
        loc = loc or ["body", "changes", *exc.path.split(".")]
        raise _unprocessable([{"loc": loc, "msg": exc.code, "type": exc.code}]) from exc
    except ValidationError as exc:
        raise _unprocessable(
            [
                {"loc": ["body", "changes", *e["loc"]], "msg": e["msg"], "type": e["type"]}
                for e in exc.errors(include_url=False)
            ]
        ) from exc
    return SettingsPatchOut(
        version=upd.version,
        values=upd.settings.model_dump(mode="json"),
        changed=upd.changed,
        restart_required=restart_required(upd.changed),
    )


@router.get("/settings/history", response_model=SettingsHistoryOut)
async def settings_history(
    c: Annotated[Container, Depends(container)],
    _: Annotated[SessionContext, Depends(current_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> SettingsHistoryOut:
    page, next_before = await c.reads.settings_history(limit, before)
    return SettingsHistoryOut(
        items=[
            SettingsVersionOut(
                version=r.version,
                changed_by=r.changed_by,
                changed_at=r.changed_at,
                changes=r.changes,
            )
            for r in page
        ],
        next_before=next_before,
    )
