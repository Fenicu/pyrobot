"""Гаджеты: покупка в магазине (на нехватку — продажа чужих акций) с надеванием, надевание
крафтового сета из рюкзака и порция заточки гаджета."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.engine.gadget_catalog import (
    SETS,
    SHOP,
    SLOTS,
    UPGRADE_KINDS,
    SetKey,
    ShopItem,
    ShopSlot,
    UpSlot,
    set_part,
    slot_of_icon,
)
from app.engine.gadgets import gear_guard, gear_until, in_dump_window, upgrade_kind
from app.engine.parsing.gadgets import (
    GadgetBought,
    GadgetWorn,
    ShopScreen,
    UpgradeAttempt,
    UpgradeConfirm,
    UpgradeScreen,
    UpgradesScreen,
)
from app.engine.parsing.items import Gadget, Gadgets, Inventory
from app.engine.parsing.screens import InfoScreen
from app.engine.parsing.stocks import StockScreen, StockSold
from app.engine.scenarios.context import (
    Predicate,
    ScenarioContext,
    ScenarioStopped,
    Step,
    StepResult,
    expect_edit,
    expect_events,
)
from app.engine.scenarios.library import (
    Params,
    ScenarioResult,
    Status,
    require,
    wrong_screen,
)
from app.engine.state.model import CharacterState, Upgrades
from app.engine.types import IncomingMessage

NETWORK = "🕸Сеть"
SHOP_BUTTON = "🏪Магазин"
INV = "/inv"
STOCK = "/stock"
UPGRADES = "/upgrades"
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
        return (
            info is not None and info.up == slot and set_part(g.slot, g.name, g.code) is SETS[key]
        )

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


def _windows(ctx: ScenarioContext) -> str | None:
    """Окно слива налички или окно-запрет по свежему состоянию: и продажа акций, и покупка ждут."""
    if in_dump_window(ctx.state(), ctx.settings(), ctx.clock.now()):
        return "dump_window"
    return _guard(ctx)[0]


def _before_buy(
    ctx: ScenarioContext, screen: ShopScreen, item: ShopItem, reserve: int
) -> str | None:
    if _mismatch(screen, item) is not None:
        return "shop_mismatch"
    if screen.money < item.price + reserve:
        return "cant_afford"
    return _windows(ctx)


async def gadget_buy(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    """Покупка тира `tier` слота `slot` по правилу `rule`: сначала товар, потом деньги — витрина
    сверяется с каталогом до продажи акций (комиссия $1/шт. не платится зря), на нехватку
    продаются чужие акции, витрина открывается снова (контекст `/buy_` и свежие деньги). Окно
    слива и окно-запрет — и до продажи, и перед `/buy_` (там же позиция и деньги с резервом
    `reserve`). `wear` — надеть купленное (в пустой слот игра надевает его сама); `in_bag` —
    неулучшенный экземпляр уже в рюкзаке: только надеть."""
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
            if (verdict := _windows(ctx)) is not None:
                return ScenarioResult("nothing", verdict)
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
                GadgetBought,
                accept=lambda e: (
                    isinstance(e, GadgetBought) and e.name.casefold() == item.name.casefold()
                ),
            ),
            deadline=_guard(ctx)[1],
        )
        if (bought.step, bought.reason) == (Step.REFUSED, "gadget_no_money"):
            return ScenarioResult("nothing", "cant_afford", {"sold": sold})
        if bought.step is not Step.OK:
            failed_buy = wrong_screen(bought)
            return ScenarioResult(failed_buy.status, failed_buy.reason, {"sold": sold})
        answer = bought.first(GadgetBought)
        details: dict[str, Any] = {
            "bought": item.name,
            "price": item.price,
            "rule": rule,
            "slot": slot,
            "tier": item.tier,
            "worn": answer is not None and answer.worn,
            "sold": sold,
        }
        if params.get("wear") and not details["worn"]:
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


# --- заточка

Hold = Callable[[], tuple[str | None, datetime | None]]


@dataclass
class _Batch:
    """Счёт порции заточки: попытки, успехи и провалы гаджета задачи, траты по видам, его
    уровень."""

    task_id: int
    level: int | None = None
    attempts: int = 0
    ok: int = 0
    fail: int = 0
    spent: dict[str, int] = field(default_factory=lambda: dict.fromkeys(UPGRADE_KINDS, 0))

    def count(self, attempt: UpgradeAttempt, own: bool) -> None:
        """Попытка потратила улучшение; успех, провал и уровень — только у гаджета задачи."""
        self.attempts += 1
        self.spent[attempt.used] = self.spent.get(attempt.used, 0) + 1
        if own:
            self.level = attempt.level
            self.ok += attempt.success
            self.fail += not attempt.success

    def result(self, status: Status, reason: str, **extra: Any) -> ScenarioResult:
        details = {
            "task_id": self.task_id,
            "level": self.level,
            "attempts": self.attempts,
            "ok": self.ok,
            "fail": self.fail,
            "spent": dict(self.spent),
            **extra,
        }
        return ScenarioResult(status, reason, details)


def _left(stocks: dict[str, int]) -> Upgrades:
    return Upgrades(**{kind: stocks.get(kind, 0) for kind in UPGRADE_KINDS})


def _upgrade_hold(ctx: ScenarioContext, task_id: int, slot: str, until: datetime | None) -> Hold:
    """Сверка перед каждым кликом заточки: задача `task_id` всё ещё активна на слоте (иначе
    `task_changed`), нет окна-запрета по свежему состоянию (иначе вердикт). Второе — `deadline`
    клика: ближайшее из `until` и начала окна-запрета."""

    def hold() -> tuple[str | None, datetime | None]:
        task = ctx.settings().gadget_upgrade
        current = task.status == "active" and task.slot == slot
        if not current or task.task_id != task_id or ctx.task_id != task_id:
            return "task_changed", None
        verdict, gear = _guard(ctx)
        if verdict is not None:
            return verdict, None
        return None, min((t for t in (until, gear) if t is not None), default=None)

    return hold


async def _attempt(
    ctx: ScenarioContext,
    frame: IncomingMessage,
    data: str,
    deadline: datetime | None,
    hold: Hold,
    gadget: str,
) -> StepResult:
    """Попытка: клик вида с кадра `frame`; игра просит подтверждения — `…_1_accept` с кадра
    правки-подтверждения, если в её шапке гаджет `gadget` и новая сверка прошла. Итог
    принимается только правкой этого сообщения. Запрет сверки и другой гаджет — `refused
    <вердикт>` без доставки."""
    message = frame.msg_id
    step = await ctx.click(
        message,
        data,
        expect_edit(message, UpgradeAttempt, UpgradeConfirm),
        frame.revision,
        content=frame.content_hash(),
        deadline=deadline,
    )
    asked = step.first(UpgradeConfirm)
    if step.step is not Step.OK or asked is None or step.delivery is None:
        return step
    if asked.name != gadget:
        return StepResult(Step.REFUSED, "gadget_changed")
    verdict, deadline = hold()
    if verdict is not None:
        return StepResult(Step.REFUSED, verdict)
    confirm = step.delivery.msg
    return await ctx.click(
        message,
        f"{data}_1_accept",
        expect_edit(message, UpgradeAttempt),
        confirm.revision,
        content=confirm.content_hash(),
        deadline=deadline,
    )


def _click_failed(step: StepResult, hold: Hold) -> tuple[Status, str]:
    """Неудачный клик заточки. Шлюз отклонил его из-за смены задачи (гонка «Стоп» или нового
    старта с порцией) или по `deadline`, когда уже идёт окно-запрет, — штатная остановка
    `nothing`, а не неудача с паузой повтора."""
    if step.reason == "upgrade_task_changed":
        return "nothing", "task_changed"
    if step.reason == "deadline" and (verdict := hold()[0]) is not None:
        return "nothing", verdict
    failed = wrong_screen(step)
    return failed.status, failed.reason


def _of_slot(slot: str) -> Predicate:
    return expect_events(
        UpgradeScreen, accept=lambda e: isinstance(e, UpgradeScreen) and e.up_slot == slot
    )


async def gadget_upgrade(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    """Порция заточки задачи `task_id`: `/upgrades` (гаджет на слоте, цель, запасы) → `/up_<slot>`
    → до `batch` попыток с кадра сообщения `/up_`. Перед каждым кликом — сверка задачи и
    окна-запрета (`_upgrade_hold`) и вид по локальному запасу; кадр следующей попытки — правка
    итога. Итог — `{task_id, level, attempts, ok, fail, spent}`; порция кончилась — `done batch`
    (решает планировщик)."""
    task_id, slot, gadget = int(params["task_id"]), str(params["slot"]), str(params["gadget"])
    target, kind = int(params["target"]), str(params["kind"])
    white_until = int(params["white_until"])
    until = datetime.fromisoformat(params["until"]) if params.get("until") else None
    hold = _upgrade_hold(ctx, task_id, slot, until)
    batch = _Batch(task_id)
    async with ctx.lease("gadget_upgrade"):
        opened = require(await ctx.send(UPGRADES, expect_events(UpgradesScreen)))
        screen = opened.first(UpgradesScreen)
        if screen is None or opened.delivery is None:
            raise ScenarioStopped("unexpected_screen", opened)
        worn = next((g for s, g in screen.items if s == slot), None)
        if worn is None or worn.name != gadget:
            return batch.result("nothing", "gadget_changed")
        level = batch.level = worn.level or 0
        if level >= target:
            return batch.result("done", "target_reached")
        if upgrade_kind(kind, level, _left(screen.stocks), white_until) is None:
            seen = opened.delivery.msg.date.isoformat()
            return batch.result("done", "exhausted", exhausted_seen_at=seen)
        shown = require(await ctx.send(f"/up_{slot}", _of_slot(slot)))
        up = shown.first(UpgradeScreen)
        if up is None or shown.delivery is None:
            raise ScenarioStopped("unexpected_screen", shown)
        if up.name != gadget:
            return batch.result("nothing", "gadget_changed")
        level = batch.level = up.level or 0
        stocks, frame = dict(up.stocks), shown.delivery.msg
        for i in range(int(params["batch"])):
            if level >= target:
                break
            if i:
                await ctx.safe_point()
            verdict, deadline = hold()
            if verdict is not None:
                return batch.result("nothing", verdict)
            use = upgrade_kind(kind, level, _left(stocks), white_until)
            if use is None:
                return batch.result("done", "exhausted")
            data = f"up_{slot}_{UPGRADE_KINDS[use][1]}"
            step = await _attempt(ctx, frame, data, deadline, hold, gadget)
            if _local(step):
                return batch.result("nothing", step.reason)
            if step.step is not Step.OK:
                return batch.result(*_click_failed(step, hold))
            attempt = step.first(UpgradeAttempt)
            if attempt is None or step.delivery is None:
                return batch.result("stopped", "unexpected_screen")
            own = attempt.name == gadget
            batch.count(attempt, own)
            stocks[attempt.used] = max(stocks.get(attempt.used, 0) - 1, 0)
            if not own:
                return batch.result("nothing", "gadget_changed")
            level, frame = attempt.level, step.delivery.msg
    if level >= target:
        return batch.result("done", "target_reached")
    return batch.result("done", "batch")
