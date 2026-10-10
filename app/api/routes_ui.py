from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Depends, Response, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from app.api.container import Container
from app.api.deps import SessionContext, container, current_session, require_csrf
from app.api.errors import AUTH, CSRF

router = APIRouter(prefix="/api/v1/me/ui", tags=["ui"])

HOME_LAYOUT_KEY = "home_layout"
ACCOUNT_ORDER_KEY = "account_order"
MAX_ORDERED_ACCOUNTS = 500
GRID_COLUMNS = 12
MAX_BLOCKS = 50
_BLOCK_ID = r"^[a-z_]{1,32}$"

BlockId = Annotated[str, StringConstraints(pattern=_BLOCK_ID)]
AccountId = Annotated[int, Field(strict=True, gt=0)]


class LayoutItem(BaseModel):
    """Блок главной: положение и размер в клетках сетки."""

    model_config = ConfigDict(extra="forbid")

    id: BlockId
    x: int = Field(ge=0, le=GRID_COLUMNS - 1)
    y: int = Field(ge=0, le=500)
    w: int = Field(ge=1, le=GRID_COLUMNS)
    h: int = Field(ge=1, le=50)

    @model_validator(mode="after")
    def _fits_grid(self) -> Self:
        if self.x + self.w > GRID_COLUMNS:
            raise ValueError("x + w exceeds grid width")
        return self


class HomeLayout(BaseModel):
    """Раскладка главной админки: видимые блоки и скрытые id. Неизвестные id принимаются:
    их отсеивает клиент."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    items: list[LayoutItem] = Field(max_length=MAX_BLOCKS)
    hidden: list[BlockId] = Field(max_length=MAX_BLOCKS)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [i.id for i in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate item id")
        if len(set(self.hidden)) != len(self.hidden):
            raise ValueError("duplicate hidden id")
        if set(ids) & set(self.hidden):
            raise ValueError("hidden overlaps items")
        return self


class HomeLayoutOut(BaseModel):
    """`layout` - `null`, пока пользователь ничего не сохранял."""

    layout: HomeLayout | None


@router.get("/home-layout", response_model=HomeLayoutOut, responses=AUTH)
async def get_home_layout(
    ctx: Annotated[SessionContext, Depends(current_session)],
    c: Annotated[Container, Depends(container)],
) -> HomeLayoutOut:
    data = await c.ui_prefs.get(ctx.user_id, HOME_LAYOUT_KEY)
    if data is None:
        return HomeLayoutOut(layout=None)
    try:
        return HomeLayoutOut(layout=HomeLayout.model_validate(data))
    except ValidationError:
        # Запись другой версии (после отката сервера): страница покажет раскладку по умолчанию.
        return HomeLayoutOut(layout=None)


@router.put("/home-layout", status_code=status.HTTP_204_NO_CONTENT, responses=CSRF)
async def put_home_layout(
    body: HomeLayout,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> Response:
    data: dict[str, Any] = body.model_dump(mode="json")
    await c.ui_prefs.put(ctx.user_id, HOME_LAYOUT_KEY, data)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class AccountOrder(BaseModel):
    """Порядок аккаунтов в списках админки. Неизвестные и чужие id принимаются: это только
    настройка пользователя, клиент отсеивает их по своему списку."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    ids: list[AccountId] = Field(max_length=MAX_ORDERED_ACCOUNTS)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("duplicate id")
        return self


class AccountOrderOut(BaseModel):
    """`order` - `null`, пока пользователь ничего не сохранял."""

    order: AccountOrder | None


@router.get("/account-order", response_model=AccountOrderOut, responses=AUTH)
async def get_account_order(
    ctx: Annotated[SessionContext, Depends(current_session)],
    c: Annotated[Container, Depends(container)],
) -> AccountOrderOut:
    data = await c.ui_prefs.get(ctx.user_id, ACCOUNT_ORDER_KEY)
    if data is None:
        return AccountOrderOut(order=None)
    try:
        return AccountOrderOut(order=AccountOrder.model_validate(data))
    except ValidationError:
        return AccountOrderOut(order=None)


@router.put("/account-order", status_code=status.HTTP_204_NO_CONTENT, responses=CSRF)
async def put_account_order(
    body: AccountOrder,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    c: Annotated[Container, Depends(container)],
) -> Response:
    await c.ui_prefs.put(ctx.user_id, ACCOUNT_ORDER_KEY, body.model_dump(mode="json"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
