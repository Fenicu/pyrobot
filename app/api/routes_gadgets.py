"""Гаджеты при персонаже: надетые, сеты, рюкзак, запасы улучшений, план покупки и задача заточки
с ходом по журналу прихода. Запись задачи — секция настроек `gadget_upgrade`: меняют её эти пути и
движок (правка видна кадром `settings` потока событий)."""

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from app.api.deps import SessionContext, require_csrf
from app.api.errors import AUTH, CSRF, ENGINE, TG_NOT_ONLINE, Responses, error
from app.api.scope import AccountScope, account_router, account_scope, running
from app.db.reads import DbReads
from app.engine.facade import EngineFacade
from app.engine.gadget_catalog import SetKey, ShopSlot, UpSlot
from app.engine.gadgets import (
    BuyAction,
    GadgetConflict,
    TargetStatus,
    TargetView,
    UpgradeTaskView,
    WearSet,
    set_of,
    shop_of,
    task_view,
    up_slot,
)
from app.engine.planner.gadgets import buy_view
from app.engine.scenarios.registry import CERTIFIED
from app.engine.settings import (
    Settings,
    UpgradeChoice,
    UpgradeStatus,
    UpSlotKey,
)
from app.engine.state.model import CharacterState, GadgetState, Obs, load_state

router = account_router("gadgets")
_WRITE: Responses = {**CSRF, **ENGINE}


class GadgetOut(BaseModel):
    # Значок слота, как в `/inv` («📱»).
    slot: str
    up_slot: UpSlot | None
    code: str | None
    name: str
    grade: str | None
    level: int | None
    bonuses: dict[str, int]
    mark: str | None
    # Крафтовый сет, к которому относится гаджет (ключ каталога).
    set: SetKey | None
    # Тир магазина у неулучшенного магазинного гаджета.
    shop_tier: int | None


class MissingPartOut(BaseModel):
    slot: ShopSlot
    tier: int
    price: int


class TargetOut(BaseModel):
    set: SetKey
    # `saving`, `ready`, `wearing` — цель покупки; `blocked` — нет 💍/💻 сета этого ранга или выше;
    # `worn_inactive` — набран, строки сета в `/inv` нет; `unconfirmed` — набран, строка сета
    # неизвестна (Um-сет); `level` — части не по уровню.
    status: TargetStatus
    # Некупленные части от дешёвой к дорогой.
    missing: list[MissingPartOut]
    in_bag: list[UpSlot]
    worn: list[UpSlot]
    blocked_by: list[UpSlot]
    # Сколько не хватает на следующую (самую дешёвую) некупленную часть, не на весь сет.
    need_money: int


class BuyActionOut(BaseModel):
    type: Literal["buy"] = "buy"
    # empty — пустой слот, set — часть цели, replace — замена надетого на более сильный.
    rule: Literal["empty", "set", "replace"]
    slot: ShopSlot
    tier: int
    price: int
    wear: bool
    # Сколько денег добрать продажей акций.
    sell_needed: int
    # Неулучшенный экземпляр уже в рюкзаке: надеть без покупки.
    in_bag: bool


class WearSetOut(BaseModel):
    type: Literal["wear_set"] = "wear_set"
    set: SetKey
    slots: list[UpSlot]


class BuyPlanOut(BaseModel):
    action: BuyActionOut | WearSetOut | None = Field(discriminator="type")
    # `chosen` или причина без действия (`bag_full`, `saving`, `cant_afford`, …).
    verdict: str
    target: TargetOut | None
    candidates: list[TargetOut]


class MoneyOut(BaseModel):
    cash: int | None
    # Чистая выручка за акции чужих компаний (за вычетом комиссии $1/шт).
    stocks: int
    reserve: int
    available: int | None


class BuyOut(BaseModel):
    enabled: bool
    # null — флаг выключен или резерв неизвестен (не видели Горбушку или дедлайн сна).
    plan: BuyPlanOut | None
    money: MoneyOut | None


class BagOut(BaseModel):
    used: int | None
    cap: int | None


class UpgradesOut(BaseModel):
    white: int
    blue: int
    red: int


class UpgradeInfoOut(BaseModel):
    # Шансы попытки по видам улучшений, %.
    chances: dict[str, int]
    upgrademan_pct: int | None
    # Режим подтверждения попытки в игре; null — неизвестен.
    confirm: bool | None


class UpgradeTaskOut(BaseModel):
    status: UpgradeStatus
    task_id: int
    slot: UpSlotKey | None
    gadget: str | None
    kind: UpgradeChoice | None
    target: int | None
    start_level: int | None
    end_level: int | None
    started_at: datetime | None
    ended_at: datetime | None
    end_reason: str | None
    # Уровень гаджета задачи сейчас; null — на слоте другой гаджет или пусто.
    level: int | None


class UpgradeSpentOut(BaseModel):
    white: int
    blue: int
    red: int


class ProgressOut(BaseModel):
    attempts: int
    ok: int
    fail: int
    spent: UpgradeSpentOut
    level: int | None


class GadgetsOut(BaseModel):
    now: datetime
    worn: list[GadgetOut]
    # Строки сетов из `/inv` («⚫️Сет VIP»).
    sets: list[str]
    bag: BagOut
    upgrades: UpgradesOut | None
    upgrade_info: UpgradeInfoOut | None
    buy: BuyOut
    task: UpgradeTaskOut
    # Ход задачи с её старта по журналу прихода; null — задачи не было.
    progress: ProgressOut | None


class UpgradeStartIn(BaseModel):
    slot: UpSlotKey
    target: int = Field(ge=1, le=60)
    kind: UpgradeChoice


def _gadget(item: GadgetState) -> GadgetOut:
    crafted, shop = set_of(item), shop_of(item)
    return GadgetOut(
        slot=item.slot,
        up_slot=up_slot(item),
        code=item.code,
        name=item.name,
        grade=item.grade,
        level=item.level,
        bonuses=item.bonuses,
        mark=item.mark,
        set=None if crafted is None else crafted.key,
        shop_tier=None if shop is None else shop.tier,
    )


def _target(view: TargetView) -> TargetOut:
    return TargetOut(
        set=view.set,
        status=view.status,
        missing=[MissingPartOut(slot=s, tier=t, price=p) for s, t, p in view.missing],
        in_bag=list(view.in_bag),
        worn=list(view.worn),
        blocked_by=list(view.blocked_by),
        need_money=view.need_money,
    )


def _action(action: BuyAction | WearSet | None) -> BuyActionOut | WearSetOut | None:
    if isinstance(action, WearSet):
        return WearSetOut(set=action.set, slots=list(action.slots))
    if action is None:
        return None
    return BuyActionOut(
        rule=action.rule,
        slot=action.slot,
        tier=action.tier,
        price=action.price,
        wear=action.wear,
        sell_needed=action.sell_needed,
        in_bag=action.in_bag,
    )


def _buy(settings: Settings, state: CharacterState, now: datetime) -> BuyOut:
    if not settings.features.gadgets_buy:
        return BuyOut(enabled=False, plan=None, money=None)
    certified = CERTIFIED if settings.engine.mode == "live" else None
    plan = buy_view(state, settings, now, certified=certified)
    if plan is None:
        return BuyOut(enabled=True, plan=None, money=None)
    m = plan.money
    return BuyOut(
        enabled=True,
        plan=BuyPlanOut(
            action=_action(plan.action),
            verdict=plan.verdict,
            target=None if plan.target is None else _target(plan.target),
            candidates=[_target(v) for v in plan.candidates],
        ),
        money=MoneyOut(cash=m.cash, stocks=m.stocks, reserve=m.reserve, available=m.available),
    )


def _value[T](seen: Obs[T] | None) -> T | None:
    return None if seen is None else seen.value


async def _out(
    reads: DbReads, settings: Settings, state: CharacterState, task: UpgradeTaskView
) -> GadgetsOut:
    now = datetime.now(UTC)
    gadgets = _value(state.gadgets)
    stocks, info = _value(state.upgrades), _value(state.upgrade_info)
    progress = None
    if task.slot is not None and task.started_at is not None:
        p = await reads.upgrade_progress(task.slot, task.started_at, task.ended_at)
        progress = ProgressOut(
            attempts=p.attempts,
            ok=p.ok,
            fail=p.fail,
            spent=UpgradeSpentOut(**p.spent),
            level=task.level,
        )
    return GadgetsOut(
        now=now,
        worn=[] if gadgets is None else [_gadget(g) for g in gadgets.items],
        sets=[] if gadgets is None else list(gadgets.sets),
        bag=BagOut(used=_value(state.bag), cap=_value(state.bag_cap)),
        upgrades=None if stocks is None else UpgradesOut(**stocks.model_dump()),
        upgrade_info=None if info is None else UpgradeInfoOut(**info.model_dump()),
        buy=_buy(settings, state, now),
        task=UpgradeTaskOut(**vars(task)),
        progress=progress,
    )


def _stored(values: dict[str, Any]) -> Settings:
    """Без движка — настройки из базы; не проходят проверку — разделы гаджетов, механики и
    задачи заточки."""
    try:
        return Settings.model_validate(values)
    except ValidationError:
        pass
    part = {k: values[k] for k in ("features", "gadgets", "gadget_upgrade") if k in values}
    try:
        return Settings.model_validate(part)
    except ValidationError:
        return Settings()


async def _live(reads: DbReads, f: EngineFacade) -> GadgetsOut:
    return await _out(reads, f.settings.current, load_state(f.pipeline.state), f.gadgets.view())


async def _act(reads: DbReads, f: EngineFacade, action: Awaitable[None]) -> GadgetsOut:
    try:
        await action
    except GadgetConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.code) from exc
    return await _live(reads, f)


@router.get("/gadgets", response_model=GadgetsOut, responses=AUTH)
async def get_gadgets(scope: Annotated[AccountScope, Depends(account_scope)]) -> GadgetsOut:
    """Гаджеты при персонаже: с движком — из него, без — из настроек и снимка состояния в базе."""
    f = scope.facade
    if f is not None:
        return await _live(scope.reads, f)
    values, _ = await scope.reads.settings()
    _, data = await scope.reads.state()
    settings, state = _stored(values), load_state(data)
    return await _out(scope.reads, settings, state, task_view(settings, state))


@router.post(
    "/gadgets/upgrade",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=GadgetsOut,
    responses={
        **_WRITE,
        409: error(TG_NOT_ONLINE, "dry_run", "upgrade_in_progress", "not_worn", "target_reached"),
    },
)
async def start_upgrade(
    body: UpgradeStartIn,
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    scope: Annotated[AccountScope, Depends(account_scope)],
    f: Annotated[EngineFacade, Depends(running)],
) -> GadgetsOut:
    """Задача заточки гаджета на слоте до уровня `target`: порции по 20 попыток идут шагом
    планировщика вне окон-запретов (метро, Горбушка, битва)."""
    return await _act(
        scope.reads, f, f.gadget_upgrade_start(body.slot, body.target, body.kind, by=ctx.login)
    )


@router.post(
    "/gadgets/upgrade/stop",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=GadgetsOut,
    responses={**_WRITE, 409: error("no_task")},
)
async def stop_upgrade(
    ctx: Annotated[SessionContext, Depends(require_csrf)],
    scope: Annotated[AccountScope, Depends(account_scope)],
    f: Annotated[EngineFacade, Depends(running)],
) -> GadgetsOut:
    """Остановить задачу заточки: следующая попытка идущей порции уже не уйдёт."""
    return await _act(scope.reads, f, f.gadget_upgrade_stop(by=ctx.login))
