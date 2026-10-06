"""Гаджеты: покупка в магазине (на нехватку — продажа чужих акций) с надеванием и надевание
крафтового сета из рюкзака."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.engine.gadget_catalog import (
    SETS,
    SHOP,
    SLOTS,
    SetKey,
    ShopItem,
    ShopSlot,
    UpSlot,
    set_by_name,
    slot_of_icon,
)
from app.engine.gadgets import gear_guard, gear_until, in_dump_window
from app.engine.parsing.gadgets import GadgetBought, GadgetWorn, ShopScreen
from app.engine.parsing.items import Gadget, Gadgets, Inventory
from app.engine.parsing.screens import InfoScreen
from app.engine.parsing.stocks import StockScreen, StockSold
from app.engine.scenarios.context import (
    Predicate,
    ScenarioContext,
    ScenarioStopped,
    Step,
    StepResult,
    expect_events,
)
from app.engine.scenarios.library import Params, ScenarioResult, require, wrong_screen
from app.engine.state.model import CharacterState

NETWORK = "🕸Сеть"
SHOP_BUTTON = "🏪Магазин"
INV = "/inv"
STOCK = "/stock"
MISSING = "missing_item"
_VS16 = "\ufe0f"

Pick = Callable[[Gadgets], Gadget | None]


def _info(name: str) -> Predicate:
    return expect_events(InfoScreen, accept=lambda e: isinstance(e, InfoScreen) and e.name == name)


async def _showcase(ctx: ScenarioContext, slot: ShopSlot) -> ShopScreen:
    """Витрина слота: `🕸Сеть` → `🏪Магазин` → кнопка слота. Кнопки — из меню предыдущего шага:
    вызывать под арендой, без безопасной точки между шагами."""
    button = SLOTS[slot].button
    assert button is not None
    require(await ctx.send(NETWORK, _info("network")))
    require(await ctx.send(SHOP_BUTTON, _info("shop")))
    step = require(
        await ctx.send(
            button,
            expect_events(
                ShopScreen, accept=lambda e: isinstance(e, ShopScreen) and e.slot == slot
            ),
        )
    )
    screen = step.first(ShopScreen)
    if screen is None:
        raise ScenarioStopped("unexpected_screen", step)
    return screen


def _mismatch(screen: ShopScreen, item: ShopItem) -> dict[str, Any] | None:
    """Позиция тира на витрине не та, что в каталоге (название, цена, требование): что видно."""
    offer = next((o for o in screen.offers if o.tier == item.tier), None)
    if offer is not None and (offer.name, offer.price, offer.level) == (
        item.name,
        item.price,
        item.level,
    ):
        return None
    seen = None
    if offer is not None:
        seen = {"name": offer.name, "price": offer.price, "level": offer.level}
    return {"slot": item.slot, "tier": item.tier, "seen": seen}


def _sales(screen: StockScreen, own: str, short: int) -> list[tuple[str, int, int]] | None:
    """Продажи чужих акций на нехватку `short` (комиссия $1/шт.): с самой дорогой, не дороже
    лимита продажи, n = ⌈нехватка / (цена − 1)⌉ с обрезкой по портфелю. None — не хватит всего
    портфеля."""
    limit = screen.max_sell or 0
    held = [
        (company, qty, price)
        for company, qty in screen.holdings.items()
        if company != own and qty > 0 and 1 < (price := screen.quotes.get(company, 0)) <= limit
    ]
    plan: list[tuple[str, int, int]] = []
    for company, qty, price in sorted(held, key=lambda h: -h[2]):
        if short <= 0:
            break
        n = min(qty, -(-short // (price - 1)))
        plan.append((company, n, price))
        short -= n * (price - 1)
    return plan if short <= 0 else None


async def _sell(
    ctx: ScenarioContext, need: int, sold: list[dict[str, Any]]
) -> ScenarioResult | None:
    """Биржа и продажа чужих акций до `need` наличных (по деньгам экрана биржи); проданное — в
    `sold`. None — денег хватает."""
    seen = ctx.state().company
    own = seen.value if seen is not None else None
    if own is None:
        # Своя компания неизвестна: любая акция может ей оказаться.
        return ScenarioResult("nothing", "cant_afford")
    opened = require(await ctx.send(STOCK, expect_events(StockScreen)))
    screen = opened.first(StockScreen)
    if screen is None:
        raise ScenarioStopped("unexpected_screen", opened)
    if screen.closed:
        return ScenarioResult("nothing", "market_closed")
    if screen.money is None or screen.max_sell is None:
        raise ScenarioStopped("unexpected_screen", opened)
    plan = _sales(screen, own, need - screen.money)
    if plan is None:
        return ScenarioResult("nothing", "cant_afford")
    # Продажи — сразу с экрана биржи, без безопасной точки.
    for company, n, price in plan:
        step = await ctx.send(f"/sells_{company}_{n}", _sold(company))
        if step.step is not Step.OK:
            failed = wrong_screen(step)
            return ScenarioResult(failed.status, failed.reason, {"sold": sold})
        sold.append({"company": company, "n": n, "price": price})
    return None


def _sold(company: str) -> Predicate:
    return expect_events(
        StockSold, accept=lambda e: isinstance(e, StockSold) and e.company == company
    )


def _fresh_copy(code: str) -> Pick:
    """Неулучшенный гаджет с кодом `code`, последний в рюкзаке: только что купленный, а не
    заточенный того же кода."""

    def pick(gadgets: Gadgets) -> Gadget | None:
        return next(
            (g for g in reversed(gadgets.bag) if g.code == code and g.grade is None),
            None,
        )

    return pick


def _set_part(key: SetKey, slot: UpSlot) -> Pick:
    """Гаджет сета `key` на слот `slot`: заточенный сильнее, при равенстве — последний."""

    def part(g: Gadget) -> bool:
        info = slot_of_icon(g.slot)
        return info is not None and info.up == slot and set_by_name(g.name) == (SETS[key], slot)

    def pick(gadgets: Gadgets) -> Gadget | None:
        found = [(g.level or 0, i, g) for i, g in enumerate(gadgets.bag) if part(g)]
        return max(found, key=lambda f: f[:2])[2] if found else None

    return pick


def _worn(name: str) -> Predicate:
    return expect_events(GadgetWorn, accept=lambda e: isinstance(e, GadgetWorn) and e.name == name)


async def _wear(ctx: ScenarioContext, pick: Pick) -> tuple[StepResult, Inventory | None]:
    """Окно-запрет по свежему состоянию → `/inv` → гаджет рюкзака по `pick` → `/wear_<N>_<код>`
    с `deadline`: надевание игра принимает только сразу после `/inv`, а номера рюкзака
    сдвигаются после каждой смены. Заглушка «неизвестная команда» — один повтор (снова запрет и
    `/inv`). Второе — рюкзак, с которого надевали. Запрет (`refused <вердикт>`) и нет нужного
    (`refused missing_item`) — шаги без ответа игры (`_local`)."""
    step = StepResult(Step.FAILED, "not_sent")
    inventory: Inventory | None = None
    for _ in range(2):
        verdict, until = _guard(ctx)
        if verdict is not None:
            return StepResult(Step.REFUSED, verdict), inventory
        opened = await ctx.send(INV, expect_events(Inventory))
        inventory = opened.first(Inventory)
        if opened.step is not Step.OK or inventory is None:
            return opened, None
        gadget = pick(inventory.gadgets)
        if gadget is None or gadget.index is None or gadget.code is None:
            return StepResult(Step.REFUSED, MISSING), inventory
        command = f"/wear_{gadget.index}_{gadget.code}"
        step = await ctx.send(command, _worn(gadget.name), deadline=until)
        if (step.step, step.reason) != (Step.REFUSED, "unknown_command"):
            break
    return step, inventory


def _local(step: StepResult) -> bool:
    """Отказ самого сценария (окно-запрет, нет в рюкзаке), а не игры: у него нет ответа."""
    return step.step is Step.REFUSED and step.delivery is None


def _guard(ctx: ScenarioContext) -> tuple[str | None, datetime | None]:
    """Окно-запрет смены снаряжения по свежему состоянию: вердикт и до какого момента смена
    разрешена (`deadline` шага)."""
    state, now = ctx.state(), ctx.clock.now()
    guard = gear_guard(state, now)
    return (guard[0] if guard is not None else None), gear_until(state, now)


def _before_buy(
    ctx: ScenarioContext, screen: ShopScreen, item: ShopItem, reserve: int
) -> str | None:
    if _mismatch(screen, item) is not None:
        return "shop_mismatch"
    if screen.money < item.price + reserve:
        return "cant_afford"
    if in_dump_window(ctx.state(), ctx.settings(), ctx.clock.now()):
        return "dump_window"
    return _guard(ctx)[0]


async def gadget_buy(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    """Покупка тира `tier` слота `slot` по правилу `rule`: сначала товар, потом деньги — витрина
    сверяется с каталогом до продажи акций (комиссия $1/шт. не платится зря), на нехватку
    продаются чужие акции, витрина открывается снова (контекст `/buy_` и свежие деньги), перед
    `/buy_` — позиция, деньги с резервом `reserve`, окно слива и окно-запрет. `wear` — надеть
    купленное; `in_bag` — неулучшенный экземпляр уже в рюкзаке: только надеть."""
    rule, slot = str(params["rule"]), params["slot"]
    item = SHOP[slot][int(params["tier"]) - 1]
    reserve = int(params["reserve"])
    async with ctx.lease("gadget_buy"):
        if params.get("in_bag"):
            return await _wear_copy(ctx, item, rule)
        screen = await _showcase(ctx, slot)
        if (mismatch := _mismatch(screen, item)) is not None:
            return ScenarioResult("nothing", "shop_mismatch", mismatch)
        sold: list[dict[str, Any]] = []
        if screen.money - reserve < item.price:
            if in_dump_window(ctx.state(), ctx.settings(), ctx.clock.now()):
                return ScenarioResult("nothing", "dump_window")
            await ctx.safe_point()
            if (failed := await _sell(ctx, item.price + reserve, sold)) is not None:
                return failed
            await ctx.safe_point()
            screen = await _showcase(ctx, slot)
        if (verdict := _before_buy(ctx, screen, item, reserve)) is not None:
            seen = _mismatch(screen, item) if verdict == "shop_mismatch" else None
            return ScenarioResult("nothing", verdict, {**(seen or {}), "sold": sold})
        bought = await ctx.send(
            f"/buy_{slot}{item.tier}",
            expect_events(
                GadgetBought, accept=lambda e: isinstance(e, GadgetBought) and e.name == item.name
            ),
            deadline=_guard(ctx)[1],
        )
        if (bought.step, bought.reason) == (Step.REFUSED, "gadget_no_money"):
            return ScenarioResult("nothing", "cant_afford", {"sold": sold})
        if bought.step is not Step.OK:
            failed_buy = wrong_screen(bought)
            return ScenarioResult(failed_buy.status, failed_buy.reason, {"sold": sold})
        details: dict[str, Any] = {
            "bought": item.name,
            "price": item.price,
            "rule": rule,
            "slot": slot,
            "tier": item.tier,
            "worn": False,
            "sold": sold,
        }
        if params.get("wear"):
            # Куплено: что бы ни случилось при надевании, итог — покупка (наденет следующий).
            try:
                await ctx.safe_point()
                worn = await _wear_bought(ctx, item)
            except ScenarioStopped as stop:
                worn = stop.reason
            if worn is None:
                details["worn"] = True
            else:
                details["wear"] = worn
    return ScenarioResult("done", "bought", details)


async def _wear_bought(ctx: ScenarioContext, item: ShopItem) -> str | None:
    """Надеть только что купленное; None — надето, иначе почему нет."""
    step, _ = await _wear(ctx, _fresh_copy(item.code))
    if step.step is Step.OK:
        return None
    return step.reason if _local(step) else wrong_screen(step).reason


async def _wear_copy(ctx: ScenarioContext, item: ShopItem, rule: str) -> ScenarioResult:
    step, _ = await _wear(ctx, _fresh_copy(item.code))
    if step.step is Step.OK:
        details = {
            "gadget": item.name,
            "rule": rule,
            "slot": item.slot,
            "tier": item.tier,
            "worn": True,
        }
        return ScenarioResult("done", "worn", details)
    if _local(step):
        return ScenarioResult("nothing", step.reason)
    return wrong_screen(step)


def _plain(line: str) -> str:
    return line.replace(_VS16, "")


async def gadget_wear_set(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    """Надеть оставшиеся в рюкзаке части сета `set` на слоты `slots`: перед каждым — окно-запрет
    по свежему состоянию (запрет — `nothing <вердикт>`, продолжит следующий запуск), свежий
    `/inv` и `/wear_`. Итог — строки сетов до (первый `/inv`) и после (хвост последнего ответа);
    `active` — строка сета в хвосте, у сета без известной строки — None."""
    key: SetKey = params["set"]
    slots: list[UpSlot] = list(params.get("slots") or ())
    if not slots:
        return ScenarioResult("nothing", MISSING)
    before: tuple[str, ...] | None = None
    after: tuple[str, ...] = ()
    async with ctx.lease("gadget_wear_set"):
        for i, slot in enumerate(slots):
            if i:
                await ctx.safe_point()
            step, inventory = await _wear(ctx, _set_part(key, slot))
            if before is None and inventory is not None:
                before = inventory.gadgets.sets
            if step.step is not Step.OK:
                if _local(step):
                    return ScenarioResult("nothing", step.reason)
                return wrong_screen(step)
            answer = step.first(Inventory)
            after = answer.gadgets.sets if answer is not None else ()
    line = SETS[key].line
    active = None if line is None else _plain(line) in {_plain(s) for s in after}
    details = {
        "set": key,
        "active": active,
        "sets_before": list(before or ()),
        "sets": list(after),
    }
    return ScenarioResult("done", "worn", details)
