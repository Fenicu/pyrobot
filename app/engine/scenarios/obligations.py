from __future__ import annotations

from app.engine.gametime import tasks_day
from app.engine.gateway.types import Predicate
from app.engine.market import dump_size, pick_stock
from app.engine.parsing.battle import BattleTargetSet
from app.engine.parsing.bulls import BullsJoined, BullsRefused
from app.engine.parsing.crew import CrewScreen, FactoryReport, FactoryScreen, FactorySignup
from app.engine.parsing.refusals import Busy, Refused
from app.engine.parsing.screens import BattleMenu
from app.engine.parsing.smoothie import (
    INGREDIENTS,
    SmoothieCooked,
    SmoothieCooking,
    SmoothieScreen,
    recipe_need,
)
from app.engine.parsing.stocks import StockBought, StockScreen
from app.engine.parsing.tangerine import TangerineRefused
from app.engine.scenarios.context import (
    ScenarioContext,
    ScenarioStopped,
    Step,
    expect_events,
)
from app.engine.scenarios.library import (
    CREW,
    Params,
    ScenarioResult,
    finish,
    require,
    wrong_screen,
)
from app.engine.state.model import CharacterState

_DROPS = {fruit: n for n, fruit in enumerate(INGREDIENTS, start=1)}


async def battle_target(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    target = str(params["target"])
    async with ctx.lease("battle_target"):
        # Кнопки целей — из меню ⚔Битва, поэтому сначала открываем меню (nav), и без безопасной
        # точки между ними.
        require(await ctx.send("⚔Битва", expect_events(BattleMenu)))
        chosen = expect_events(
            BattleTargetSet, accept=lambda e: isinstance(e, BattleTargetSet) and e.target == target
        )
        return finish(await ctx.send(target, chosen))


async def stocks_dump(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    keep, margin = int(params["keep"]), int(params["margin"])
    async with ctx.lease("stocks_dump"):
        opened = require(await ctx.send("/stock", expect_events(StockScreen)))
        screen = opened.first(StockScreen)
        if screen is None:
            raise ScenarioStopped("unexpected_screen", opened)
        if screen.closed:
            return ScenarioResult("nothing", "market_closed")
        if screen.min_buy is None or screen.max_sell is None or screen.money is None:
            raise ScenarioStopped("unexpected_screen", opened)
        pick = pick_stock(screen.quotes, screen.min_buy, screen.max_sell, margin)
        if pick is None:
            return ScenarioResult("nothing", "no_stock")
        company, price = pick
        n = dump_size(screen.money, keep, screen.reserve or 0, price)
        if n < 1:
            return ScenarioResult("nothing", "not_enough_money")
        # Покупка — сразу с экрана биржи, по его деньгам: без безопасной точки.
        bought = expect_events(
            StockBought, accept=lambda e: isinstance(e, StockBought) and e.company == company
        )
        return finish(await ctx.send(f"/buys_{company}_{n}", bought))


async def factory_signup(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    # /crew_factory игра принимает только из меню команды, 👍Записаться — только с экрана фабрики.
    # Безопасной точки между шагами нет: срочное действие между ними сбило бы экран.
    async with ctx.lease("factory_signup"):
        crew = await ctx.send(CREW, expect_events(CrewScreen))
        if crew.step is not Step.OK:
            return wrong_screen(crew)
        opened = await ctx.send("/crew_factory", expect_events(FactoryScreen))
        screen = opened.first(FactoryScreen)
        if opened.step is not Step.OK or screen is None:
            return wrong_screen(opened)
        if screen.status != "not_signed":
            return ScenarioResult("nothing", screen.status)
        step = await ctx.send("👍Записаться", expect_events(FactorySignup))
        signup = step.first(FactorySignup)
        if step.step is Step.OK and signup is not None:
            return ScenarioResult("done", signup.result)
        return wrong_screen(step)


async def factory_report(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    """Личный отчёт о битве за фабрику: игра присылает его только в ответ на `/fb` (навигация).
    Отчёт не за сегодня (`/fb` отдаёт последнюю битву с участием персонажа) — `nothing`: повтор
    через паузу цикла, пересылается только сегодняшний."""
    step = await ctx.send("/fb", expect_events(FactoryReport))
    report = step.first(FactoryReport)
    if step.step is not Step.OK or report is None or step.delivery is None:
        return wrong_screen(step)
    if report.battle_day != tasks_day(step.delivery.msg.origin):
        return ScenarioResult("nothing", "old_report")
    return ScenarioResult("done", "won" if report.won else "lost")


async def bulls_join(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    answer = expect_events(BullsJoined, refuse=(Refused, Busy, BullsRefused))
    return finish(await ctx.send(str(params["code"]), answer))


async def tangerine(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    # Об успехе игра в личку молчит, ошибка приходит за секунды: тишина — отправлено. Отказом
    # считаем только ответ про мандарины: чужой «занят» или отказ в это окно — не про /gt.
    errors = expect_events(refuse=(TangerineRefused,))
    step = await ctx.send(
        "/gt",
        errors,
        chat_id=int(params["chat"]),
        reply_to=int(params["reply_to"]),
        silence_confirms=True,
    )
    if step.step is Step.OK and step.reason == "silence":
        return ScenarioResult("done", "no_error")
    return finish(step)


async def smoothie(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    recipe = str(params["recipe"])
    async with ctx.lease("smoothie"):
        opened = require(await ctx.send("/smoothie", expect_events(SmoothieScreen)))
        screen = opened.first(SmoothieScreen)
        if screen is None:
            raise ScenarioStopped("unexpected_screen", opened)
        if screen.bonus is not None:
            return ScenarioResult("nothing", "cooked_today")
        if any(screen.ingredients.get(name, 0) < n for name, n in recipe_need(recipe).items()):
            return ScenarioResult("nothing", "no_ingredients")
        # 🍹Готовить — кнопка экрана Смузийной: без безопасной точки. Фрукты — кнопки сообщения
        # варки, между ними точка есть.
        cooking = require(await ctx.send("🍹Готовить", _dropped("")))
        if cooking.delivery is None:
            raise ScenarioStopped("unexpected_screen", cooking)
        message = cooking.delivery.msg.msg_id
        for i, fruit in enumerate(recipe, start=1):
            await ctx.safe_point()
            require(await ctx.click(message, f"sm_drop_{_DROPS[fruit]}", _dropped(recipe[:i])))
        await ctx.safe_point()
        step = await ctx.click(message, "smoothie_accept", expect_events(SmoothieCooked))
        cooked = step.first(SmoothieCooked)
        if step.step is Step.OK and cooked is not None and cooked.bonus is None:
            return ScenarioResult("done", "no_bonus")
        return finish(step)


def _dropped(fruits: str) -> Predicate:
    # Экран варки правится после каждого фрукта: ждём правку, где вброшено ровно столько.
    return expect_events(
        SmoothieCooking,
        accept=lambda e: (
            isinstance(e, SmoothieCooking) and e.dropped == fruits and not e.cancelled
        ),
    )
