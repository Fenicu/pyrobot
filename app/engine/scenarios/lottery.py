from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from app.engine.bus import Delivery
from app.engine.gateway.types import Match, Predicate, Verdict
from app.engine.parsing.lottery import LotteryBought, LotteryCurrency, LotteryOff, LotteryScreen
from app.engine.parsing.refusals import Busy, Refused
from app.engine.scenarios.context import (
    ScenarioContext,
    ScenarioStopped,
    Step,
    expect_events,
)
from app.engine.scenarios.library import Params, ScenarioResult, finish, wrong_screen
from app.engine.state.model import CharacterState
from app.engine.state.reducer import LOTTERY_CURRENCIES, LOTTERY_SALE_ENDS
from app.engine.types import IncomingMessage

SCREEN = "/tickets"
BUY_ALL = "/tickets_all"
BUY_ONE = {"money": "💵 => 🤑", "knowledge": "📚 => 🤑", "raw": "🔩 => 🤑", "details": "⚙️ => 🤑"}
Count = int | Literal["max"]


class BadParam(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Goal:
    """Цель по валюте на этот тираж: `need` — сколько докупить до цели, `can` — сколько из них по
    карману сверх запаса и резервов, `target` — сколько должно стать куплено (куплено по экрану +
    `can`), `to_limit` — остаток до лимита тиража."""

    need: int
    can: int
    target: int
    to_limit: int


def _count(params: Params, key: str) -> Count:
    value = params.get(key, "max")
    if value == "max":
        return "max"
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise BadParam(key)


def _amount(params: Params, key: str) -> int:
    value = params.get(key, 0)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise BadParam(key)


def goals(screen: LotteryScreen, params: Params) -> dict[str, Goal]:
    """`tickets_<валюта>` — число или `max` (по умолчанию), `keep_<валюта>` — запас ресурса,
    `reserve` — ещё запас 💵 (билет Горбушки, отель); BadParam — недопустимое значение."""
    reserve = _amount(params, "reserve")
    out: dict[str, Goal] = {}
    for c in LOTTERY_CURRENCIES:
        limit, bought, price = screen.limits[c], screen.bought[c], screen.prices[c]
        tickets = _count(params, f"tickets_{c}")
        want = limit if tickets == "max" else min(tickets, limit)
        need = max(want - bought, 0)
        have = screen.resources[c]
        free = have - _amount(params, f"keep_{c}") - (reserve if c == "money" else 0)
        can = min(need, max(free, 0) // price) if price > 0 else need
        to_limit = max(limit - bought, 0)
        out[c] = Goal(need=need, can=can, target=bought + can, to_limit=to_limit)
    return out


def buys_all(goals: Mapping[str, Goal]) -> bool:
    """«Купить все» — только когда по каждой валюте цель — весь остаток до лимита и на неё хватает
    сверх запасов и резервов: игра не знает ни целей, ни запасов и купит всё, на что хватит денег в
    момент отправки."""
    return all(g.need == g.to_limit and g.can == g.need for g in goals.values())


def sale_ends(screen: LotteryScreen, delivery: Delivery) -> datetime:
    return delivery.msg.date + timedelta(seconds=screen.draw_in_s) - LOTTERY_SALE_ENDS


def _screen() -> Predicate:
    return expect_events(LotteryScreen, LotteryOff)


def _bought(draw: int) -> Predicate:
    base = expect_events(LotteryBought, refuse=(Refused, Busy, LotteryOff))

    def predicate(delivery: Delivery) -> Match | None:
        for event in delivery.events:
            if isinstance(event, LotteryBought) and event.draw != draw:
                return Match(Verdict.REFUSED, "draw_changed")
        return base(delivery)

    return predicate


async def lottery_buy(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    """Билеты тиража по валютам: экран `/tickets` → если «Купить все» купит ровно задуманное —
    `/tickets_all`, иначе покупка по валютам. Повтор безопасен: сценарий начинается с экрана."""
    async with ctx.lease("lottery"):
        opened = await _open(ctx)
        if isinstance(opened, ScenarioResult):
            return opened
        screen, until = opened
        try:
            plan = goals(screen, params)
        except BadParam as bad:
            return ScenarioResult("failed", f"bad_param:{bad}")
        missing = {c: g for c, g in plan.items() if g.need > 0}
        if not missing:
            return ScenarioResult("nothing", "target_reached")
        if not any(g.can > 0 for g in missing.values()):
            return ScenarioResult("nothing", "cant_afford")
        if not buys_all(plan):
            return await buy_each(ctx, screen, params, until)
        if ctx.clock.now() >= until:
            return ScenarioResult("nothing", "sale_closed")
        step = await ctx.send(BUY_ALL, _bought(screen.draw))
        if step.step is Step.OK and step.first(LotteryBought) is not None:
            return ScenarioResult("done", "bought_all")
        return finish(step)


async def _open(ctx: ScenarioContext) -> tuple[LotteryScreen, datetime] | ScenarioResult:
    """Экран тиража и срок продажи по его отсчёту."""
    opened = await ctx.send(SCREEN, _screen())
    if opened.step is Step.REFUSED and opened.reason == "lottery_closed":
        return ScenarioResult("nothing", "lottery_closed")
    if opened.step is not Step.OK or opened.delivery is None:
        return wrong_screen(opened)
    if opened.first(LotteryOff) is not None:
        return ScenarioResult("nothing", "no_draw")
    screen = opened.first(LotteryScreen)
    if screen is None:
        raise ScenarioStopped("unexpected_screen", opened)
    return screen, sale_ends(screen, opened.delivery)


QUANTITY = re.compile(r"tickets_\w+_(?P<n>\d+)\Z")


def quantity(msg: IncomingMessage, left: int) -> tuple[int, str] | None:
    """Кнопка количества для покупки: наибольшая не больше `left` (кнопки — 1/3/5/7 и остаток
    до лимита, «всех» нет); callback берётся с самой кнопки — имена валют в них не угадываем."""
    options = [
        (int(m["n"]), b.data)
        for b in msg.inline
        if b.data is not None and (m := QUANTITY.match(b.data)) is not None
    ]
    fitting = [o for o in options if o[0] <= left]
    return max(fitting) if fitting else None


def _currency_screen(currency: str) -> Predicate:
    return expect_events(
        LotteryCurrency,
        accept=lambda e: isinstance(e, LotteryCurrency) and e.currency == currency,
        refuse=(Refused, Busy, LotteryOff),
    )


def _clicked(message_id: int, currency: str, before: int) -> Predicate:
    """Итог клика — правка того же экрана валюты, где куплено больше, чем было; ответа на
    callback игра не даёт. «Не хватает» вместо покупки — отказ."""

    def predicate(delivery: Delivery) -> Match | None:
        for event in delivery.events:
            if isinstance(event, Refused | Busy):
                return Match(Verdict.REFUSED, str(getattr(event, "reason", None) or event.kind))
        if delivery.msg.msg_id != message_id:
            return None
        for event in delivery.events:
            if not isinstance(event, LotteryCurrency) or event.currency != currency:
                continue
            if event.bought > before:
                return Match(Verdict.CONFIRMED, "bought")
            if event.short:
                return Match(Verdict.REFUSED, "no_money")
        return None

    return predicate


async def buy_each(
    ctx: ScenarioContext, screen: LotteryScreen, params: Params, until: datetime
) -> ScenarioResult:
    """Покупка по валютам: `<валюта> => 🤑` → экран валюты с кнопками количества → клики, пока
    не куплено задуманное. Между валютами — безопасная точка, после неё экран, номер тиража и
    цели перечитываются: срочное действие могло потратить ресурс. Остановка посреди — итог
    остановки, купленное до неё видно в состоянии."""
    plan = goals(screen, params)
    total, first = 0, True
    for currency in LOTTERY_CURRENCIES:
        if plan[currency].can <= 0:
            continue
        if not first:
            await ctx.safe_point()
            opened = await _open(ctx)
            if isinstance(opened, ScenarioResult):
                return opened
            fresh, until = opened
            if fresh.draw != screen.draw:
                return ScenarioResult("nothing", "draw_changed")
            plan = goals(fresh, params)
            if plan[currency].can <= 0:
                continue
        first = False
        got = await _buy_currency(ctx, currency, plan[currency].target, until)
        if isinstance(got, ScenarioResult):
            return got
        total += got
    return (
        ScenarioResult("done", "bought_each")
        if total
        else ScenarioResult("nothing", "cant_afford")
    )


async def _buy_currency(
    ctx: ScenarioContext, currency: str, target: int, until: datetime
) -> int | ScenarioResult:
    """Сколько билетов валюты куплено сейчас (0 — цель уже достигнута, на лимите или не хватает
    по мнению игры) или итог, на котором сценарий останавливается. `target` — сколько должно
    стать куплено всего: остаток считается по «Куплено» самого экрана валюты — с телефона могли
    докупить после экрана тиража."""
    if ctx.clock.now() >= until:
        return ScenarioResult("nothing", "sale_closed")
    opened = await ctx.send(BUY_ONE[currency], _currency_screen(currency))
    view = opened.first(LotteryCurrency)
    if opened.step is not Step.OK or view is None or opened.delivery is None:
        return wrong_screen(opened)
    msg = opened.delivery.msg
    got = 0
    while view.bought < target and not (view.short or view.full):
        choice = quantity(msg, target - view.bought)
        if choice is None:
            break
        _, data = choice
        if ctx.clock.now() >= until:
            return ScenarioResult("nothing", "sale_closed")
        step = await ctx.click(
            msg.msg_id,
            data,
            _clicked(msg.msg_id, currency, view.bought),
            msg.revision,
            content=msg.content_hash(),
        )
        after = step.first(LotteryCurrency)
        if step.step is Step.REFUSED and step.reason == "no_money":
            break
        if step.step is not Step.OK or after is None or step.delivery is None:
            return finish(step)
        got += after.bought - view.bought
        view, msg = after, step.delivery.msg
    return got
