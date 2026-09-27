import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.api.container import Container
from app.api.deps import SessionContext, container, current_session, require_csrf
from app.api.routes_engine import facade
from app.engine.commands import CommandClass, classify_callback, classify_text
from app.engine.facade import EngineFacade, PlannerUnavailable
from app.engine.gateway.gateway import STORE_FAILED
from app.engine.gateway.types import ActionRequest, ActionStatus
from app.engine.manual import (
    Confirm,
    KeyReused,
    click_request,
    fingerprint,
    manual_key,
    send_request,
)
from app.engine.planner.loop import FixedParams
from app.engine.scenarios.registry import SCENARIOS

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["commands"])
KEY_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"
_NEVER = (CommandClass.FORBIDDEN, CommandClass.DONATE)
MAX_SCENARIO_PARAMS = 16


class SendIn(BaseModel):
    text: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(pattern=KEY_PATTERN)
    confirm_token: str | None = Field(default=None, max_length=128)


class ClickIn(BaseModel):
    chat_id: int
    message_id: int
    revision: int
    # Telegram ограничивает callback_data 64 байтами.
    callback_data: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(pattern=KEY_PATTERN)
    confirm_token: str | None = Field(default=None, max_length=128)


class CommandOut(BaseModel):
    action_id: int | None
    status: str
    reason: str
    answer: str | None = None


class ConfirmRequired(BaseModel):
    code: Literal["confirm_required"] = "confirm_required"
    reason: Literal["missing", "invalid", "expired"]
    confirm_token: str
    expires_at: datetime
    state_version: int
    command_class: str


ParamValue = str | int | float | bool | None


class ScenarioRunIn(BaseModel):
    params: dict[str, ParamValue] = Field(default_factory=dict)
    idempotency_key: str = Field(pattern=KEY_PATTERN)


class ScenarioRunAccepted(BaseModel):
    scenario_run_id: int
    status: str


class ScenarioInfo(BaseModel):
    name: str
    certified: bool
    params: dict[str, Any]


_RESPONSES: dict[int | str, dict[str, object]] = {
    202: {"model": CommandOut, "description": "Not finished yet; repeat with the same key"},
    403: {"description": "forbidden or donate command"},
    409: {"model": ConfirmRequired, "description": "risky command needs confirm_token"},
    422: {"description": "invalid body or idempotency_key reused with other parameters"},
    503: {"description": "idempotency record not stored; retry with the same key"},
}


async def _execute(
    c: Container,
    f: EngineFacade,
    ctx: SessionContext,
    response: Response,
    *,
    cls: CommandClass,
    key: str,
    token: str | None,
    params: Sequence[object],
    build: Callable[[str | None, Confirm | None], ActionRequest],
) -> CommandOut:
    if cls in _NEVER:
        # Попытка остаётся в журнале действий (без ключа — он не расходуется).
        await f.manual(build(None, None), wait_s=c.command_wait_s)
        log.warning("manual %s command refused for %s", cls.value, ctx.login)
        raise HTTPException(status.HTTP_403_FORBIDDEN, cls.value)
    req = build(key, None)
    if not f.manual_pending(key):
        known = await c.reads.action_by_key(manual_key(key))
        if known is not None:
            if (known.kind, known.chat_id, known.payload) != fingerprint(req):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "idempotency_key reused"
                )
            return CommandOut(
                action_id=known.id, status=known.status, reason=known.reason, answer=known.answer
            )
        if cls is CommandClass.RISKY:
            req = build(key, _require_confirm(c, f, ctx, key, token, params, cls))
    try:
        result = await f.manual(req, wait_s=c.command_wait_s)
    except KeyReused as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "idempotency_key reused"
        ) from exc
    if result is not None and (result.status, result.reason) == (
        ActionStatus.REJECTED,
        STORE_FAILED,
    ):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, STORE_FAILED)
    if result is None:
        response.status_code = status.HTTP_202_ACCEPTED
        return CommandOut(action_id=None, status="pending", reason="")
    return CommandOut(
        action_id=result.action_id,
        status=result.status.value,
        reason=result.reason,
        answer=result.answer,
    )


def _require_confirm(
    c: Container,
    f: EngineFacade,
    ctx: SessionContext,
    key: str,
    token: str | None,
    params: Sequence[object],
    cls: CommandClass,
) -> Confirm:
    """Годный токен — версия состояния и срок, на которые он выдан; иначе 409 с новым."""
    version = f.state()[0]
    problem = (
        "missing"
        if token is None
        else c.confirm.check(token, ctx.session_id, key, params, version)
    )
    if problem is None and token is not None:
        return version, datetime.fromtimestamp(c.confirm.expires_at(token) or 0, UTC)
    fresh = c.confirm.issue(ctx.session_id, key, params, version)
    expires = c.confirm.expires_at(fresh) or 0
    body = ConfirmRequired(
        reason=problem,  # type: ignore[arg-type]
        confirm_token=fresh,
        expires_at=datetime.fromtimestamp(expires, UTC),
        state_version=version,
        command_class=cls.value,
    )
    raise HTTPException(status.HTTP_409_CONFLICT, body.model_dump(mode="json"))


@router.post("/commands/send", response_model=CommandOut, responses=_RESPONSES)
async def command_send(
    body: SendIn,
    response: Response,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> CommandOut:
    chat_id = f.settings.current.chats.game_chat_id
    cls = classify_text(body.text)
    return await _execute(
        c,
        f,
        ctx,
        response,
        cls=cls,
        key=body.idempotency_key,
        token=body.confirm_token,
        params=("send", chat_id, body.text),
        build=lambda key, confirm: send_request(
            body.text, chat_id=chat_id, cls=cls, key=key, confirm=confirm
        ),
    )


@router.post("/commands/click", response_model=CommandOut, responses=_RESPONSES)
async def command_click(
    body: ClickIn,
    response: Response,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> CommandOut:
    cls = classify_callback(body.callback_data)
    return await _execute(
        c,
        f,
        ctx,
        response,
        cls=cls,
        key=body.idempotency_key,
        token=body.confirm_token,
        params=("click", body.chat_id, body.message_id, body.revision, body.callback_data),
        build=lambda key, confirm: click_request(
            chat_id=body.chat_id,
            message_id=body.message_id,
            revision=body.revision,
            data=body.callback_data,
            cls=cls,
            key=key,
            confirm=confirm,
        ),
    )


@router.get("/scenarios", response_model=list[ScenarioInfo])
async def scenarios(_: Annotated[SessionContext, Depends(current_session)]) -> list[ScenarioInfo]:
    """Сценарии, доступные ручному запуску; несертифицированные исполняются как simulate."""
    return [
        ScenarioInfo(name=s.name, certified=s.certified, params=dict(s.params))
        for s in SCENARIOS.values()
    ]


@router.post(
    "/scenarios/{name}/run",
    response_model=ScenarioRunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        200: {"model": ScenarioRunAccepted, "description": "Key already used: existing run"},
        404: {"description": "unknown scenario"},
        422: {
            "description": "invalid body, params contradicting fixed scenario params, or "
            "idempotency_key reused with other parameters"
        },
    },
)
async def scenario_run(
    name: str,
    body: ScenarioRunIn,
    response: Response,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> ScenarioRunAccepted:
    if name not in SCENARIOS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown scenario")
    if len(body.params) > MAX_SCENARIO_PARAMS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "too many params")
    try:
        run_id, created = await f.run_scenario(
            name, body.params, key=body.idempotency_key, by=ctx.login
        )
    except PlannerUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "planner not started") from exc
    except FixedParams as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"fixed params: {', '.join(exc.args[0])}"
        ) from exc
    if created:
        return ScenarioRunAccepted(scenario_run_id=run_id, status="queued")
    found = await c.reads.scenario_run(run_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scenario run not found")
    row = found[0]
    if (row.scenario, row.requested_params) != (name, body.params):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "idempotency_key reused")
    response.status_code = status.HTTP_200_OK
    return ScenarioRunAccepted(scenario_run_id=run_id, status=row.status)
