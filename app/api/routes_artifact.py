"""Сбор артефакта: что идёт и что можно, запуск, пауза, отмена и «Вести сбор». Запись сбора —
секция настроек `artifact_run`: меняют её только эти пути и движок (правка видна кадром `settings`
потока событий)."""

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, ValidationError

from app.api.deps import SessionContext, require_csrf
from app.api.errors import AUTH, CSRF, ENGINE, TG_NOT_ONLINE, Responses, error
from app.api.scope import AccountScope, account_router, account_scope, running
from app.engine.artifact import ArtifactConflict, ArtifactView, artifact_view
from app.engine.facade import EngineFacade
from app.engine.settings import ArtifactDeed, ArtifactKey, RunStatus, Settings
from app.engine.state.model import load_state

router = account_router("artifact")
_WRITE: Responses = {**CSRF, **ENGINE}


class ArtifactRunOut(BaseModel):
    artifact: ArtifactKey | None
    status: RunStatus
    requested_at: datetime | None
    started_at: datetime | None
    ends_at: datetime | None
    # Где падают части по сообщению «Сбор начат!».
    deed_hint: ArtifactDeed | None
    result_level: int | None
    # Бот включил лотерею на время сбора: по окончании вернёт прежние настройки, если их не меняли.
    lottery_switched: bool


class ArtifactCollectOut(BaseModel):
    artifact: str
    ends_at: datetime


class ArtifactPaceOut(BaseModel):
    levels_per_day: float | None
    forecast_level: int | None


class ArtifactOut(BaseModel):
    now: datetime
    run: ArtifactRunOut
    # Уровень собираемого артефакта (у отменённого и завершённого — итоговый); null — неизвестен.
    level: int | None
    levels: dict[str, int]
    # Идущий в игре сбор по последнему экрану артефактов.
    collecting: ArtifactCollectOut | None
    # Идущий сбор не ведёт запись: его можно взять кнопкой «Вести сбор».
    external: bool
    # Дела, где падают части, по артефактам — при нынешнем уровне персонажа.
    tactic: dict[str, list[str]]
    lottery_on: bool
    lottery_on_start: bool
    pace: ArtifactPaceOut
    # Раньше этого момента новый сбор не начать: 10 суток с прошлого старта.
    next_start_at: datetime | None


class ArtifactStartIn(BaseModel):
    artifact: ArtifactKey
    # Лотерея выключена — включить её на время сбора: все билеты до лимита, без запасов.
    lottery_max: bool = False


def _out(view: ArtifactView, now: datetime) -> ArtifactOut:
    run = view.run
    collect = view.collecting
    return ArtifactOut(
        now=now,
        run=ArtifactRunOut(
            artifact=run.artifact,
            status=run.status,
            requested_at=run.requested_at,
            started_at=run.started_at,
            ends_at=run.ends_at,
            deed_hint=run.deed_hint,
            result_level=run.result_level,
            lottery_switched=run.lottery_applied is not None,
        ),
        level=view.level,
        levels=view.levels,
        collecting=(
            ArtifactCollectOut(artifact=collect.artifact, ends_at=collect.ends_at)
            if collect is not None
            else None
        ),
        external=view.external,
        tactic={a: list(deeds) for a, deeds in view.tactic.items()},
        lottery_on=view.lottery_on,
        lottery_on_start=view.lottery_on_start,
        pace=ArtifactPaceOut(
            levels_per_day=view.pace.levels_per_day, forecast_level=view.pace.forecast_level
        ),
        next_start_at=view.next_start_at,
    )


def _stored(values: dict[str, Any]) -> Settings:
    """Без движка — разделы сбора и флаг лотереи из базы; прочее для вида не нужно."""
    part = {k: values[k] for k in ("features", "artifacts", "artifact_run") if k in values}
    try:
        return Settings.model_validate(part)
    except ValidationError:
        return Settings()


async def _act(f: EngineFacade, action: Awaitable[None]) -> ArtifactOut:
    try:
        await action
    except ArtifactConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.code) from exc
    return _out(f.artifacts.view(), datetime.now(UTC))


@router.get("/artifact", response_model=ArtifactOut, responses=AUTH)
async def get_artifact(scope: Annotated[AccountScope, Depends(account_scope)]) -> ArtifactOut:
    """Сбор артефакта: с движком — из него, без — из настроек и снимка состояния в базе."""
    now = datetime.now(UTC)
    f = scope.facade
    if f is not None:
        return _out(f.artifacts.view(), now)
    values, _ = await scope.reads.settings()
    _, state = await scope.reads.state()
    return _out(artifact_view(_stored(values), load_state(state), now), now)


@router.post(
    "/artifact/start",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ArtifactOut,
    responses={
        **_WRITE,
        409: error(
            TG_NOT_ONLINE,
            "dry_run",
            "artifact_max",
            "run_in_progress",
            "locked_until",
            "deeds_disabled",
        ),
    },
)
async def start_artifact(
    body: ArtifactStartIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> ArtifactOut:
    """Запрос на сбор: снимок лотереи, статус `starting`. Сам сбор в игре запустит планировщик,
    как только персонаж свободен (не дело, не сон, не метро)."""
    return await _act(
        f, f.artifact_start(body.artifact, lottery_max=body.lottery_max, by=ctx.login)
    )


@router.post(
    "/artifact/pause", response_model=ArtifactOut, responses={**_WRITE, 409: error("no_run")}
)
async def pause_artifact(
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> ArtifactOut:
    """Пауза сбора: бот играет как обычно, таймер в игре идёт."""
    return await _act(f, f.artifact_pause(by=ctx.login))


@router.post(
    "/artifact/resume", response_model=ArtifactOut, responses={**_WRITE, 409: error("no_run")}
)
async def resume_artifact(
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> ArtifactOut:
    return await _act(f, f.artifact_resume(by=ctx.login))


@router.post(
    "/artifact/cancel", response_model=ArtifactOut, responses={**_WRITE, 409: error("no_run")}
)
async def cancel_artifact(
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> ArtifactOut:
    """Отмена: из `starting` запись снимается (в игре ничего не начато), из сбора — `cancelled`
    с итоговым уровнем; таймер в игре дотикает. Лотерея возвращается, если её не меняли."""
    return await _act(f, f.artifact_cancel(by=ctx.login))


@router.post(
    "/artifact/adopt",
    response_model=ArtifactOut,
    responses={**_WRITE, 409: error("run_in_progress", "not_collecting", "not_external")},
)
async def adopt_artifact(
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    f: Annotated[EngineFacade, Depends(running)],
) -> ArtifactOut:
    """«Вести сбор»: сбор, начатый в игре без бота, — конец по таймеру экрана; лотерея не
    трогается."""
    return await _act(f, f.artifact_adopt(by=ctx.login))
