from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.engine.bus import Delivery
from app.engine.gateway.types import Match, Verdict
from app.engine.parsing.activities import ActivityStarted
from app.engine.parsing.food import FastfoodEaten, FoodMenu
from app.engine.parsing.gorbushka import GorbushkaFight, GorbushkaNotice, GorbushkaScreen
from app.engine.parsing.items import BookRead, CardUsed, ContainerOpened, PrizeboxOpened
from app.engine.parsing.levelup import LevelUpStep
from app.engine.parsing.sleep import FellAsleep, SleepMenu
from app.engine.reconcile import FOOD, GIFTS, GORBUSHKA, INVENTORY, PROFILE
from app.engine.scenarios.context import (
    Predicate,
    ScenarioContext,
    ScenarioStopped,
    Step,
    StepResult,
    expect_button,
    expect_events,
)
from app.engine.state.model import CharacterState
from app.engine.types import IncomingMessage

Status = Literal["done", "nothing", "refused", "suppressed", "failed", "stopped"]


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    status: Status
    reason: str = ""


Params = Mapping[str, Any]
ScenarioFn = Callable[[ScenarioContext, CharacterState, Params], Awaitable[ScenarioResult]]

DEED_COMMANDS = {
    "harvest": "/harvest",
    "job": "/job",
    "learn": "/learns",
    "dconv": "/dconv",
    "eat": "/eat",
    "walk": "/walk",
    "confa": "/confa",
    "rob": "🔫Грабить",
}
FOOD_BUTTONS = {
    "hotdog": "🌭Хот-дог",
    "pizza": "🍕Пицца",
    "burger": "🍔Бургер",
    "banana": "🍌Банан",
}
REFRESH = {s.name: s for s in (PROFILE, INVENTORY, FOOD, GIFTS, GORBUSHKA)}


_STATUS: dict[Step, Status] = {
    Step.OK: "done",
    Step.REFUSED: "refused",
    Step.SUPPRESSED: "suppressed",
    Step.FAILED: "failed",
}


def _finish(step: StepResult) -> ScenarioResult:
    return ScenarioResult(_STATUS[step.step], step.reason)


def _require(step: StepResult) -> StepResult:
    if step.step is not Step.OK:
        raise ScenarioStopped(step.reason, step)
    return step


async def deed(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    activity = str(params["activity"])
    step = await ctx.send(
        DEED_COMMANDS[activity],
        expect_events(
            ActivityStarted,
            accept=lambda e: isinstance(e, ActivityStarted) and e.activity == activity,
        ),
    )
    return _finish(step)


_SIMPLE = {
    "book": ("/read_exp", BookRead),
    "card": ("/use_card", CardUsed),
    "prizebox": ("/unbox", PrizeboxOpened),
    "container_small": ("/unbox_ls", ContainerOpened),
    "container_medium": ("/unbox_lm", ContainerOpened),
}


async def free_item(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    command, event = _SIMPLE[str(params["item"])]
    return _finish(await ctx.send(command, expect_events(event)))


async def refresh(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    source = REFRESH[str(params["source"])]
    return _finish(await ctx.send(source.command, expect_events(source.event)))


async def fastfood(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    # Кнопки еды — из меню 🍴, поэтому сначала открываем меню (nav).
    async with ctx.lease("fastfood"):
        menu = _require(await ctx.send("/to_eat", expect_events(FoodMenu))).first(FoodMenu)
        # Кулдаун виден в самом меню: кнопка дала бы только отказ.
        if menu is not None and (menu.fastfood_in_s or 0) > 0:
            return ScenarioResult("nothing", "fastfood_cooldown")
        await ctx.safe_point()
        return _finish(
            await ctx.send(FOOD_BUTTONS[str(params["food"])], expect_events(FastfoodEaten))
        )


def _lower(a: tuple[str, int], b: tuple[str, int]) -> str:
    return a[0] if a[1] <= b[1] else b[0]


async def levelup(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    skills = state.skills.value if state.skills is not None else None
    main = "+1 🔨Практика"
    extra = "+1 🐿Хитрость"
    if skills is not None:
        main = _lower(("+1 🔨Практика", skills.practice), ("+1 🎓Теория", skills.theory))
        extra = _lower(("+1 🐿Хитрость", skills.cunning), ("+1 🐢Мудрость", skills.wisdom))

    def step(name: str) -> Predicate:
        return expect_events(
            LevelUpStep, accept=lambda e: isinstance(e, LevelUpStep) and e.step == name
        )

    async with ctx.lease("levelup"):
        _require(await ctx.send("/levelup", step("menu")))
        await ctx.safe_point()
        _require(await ctx.send(main, step("main_skill")))
        await ctx.safe_point()
        return _finish(await ctx.send(extra, step("done")))


def _gorbushka_screen(step: StepResult) -> tuple[GorbushkaScreen, IncomingMessage]:
    screen = _require(step).first(GorbushkaScreen)
    if screen is None or step.delivery is None:
        raise ScenarioStopped("unexpected_screen", step)
    return screen, step.delivery.msg


def _affordable(s: GorbushkaScreen) -> bool:
    # Билет покупается по ресурсам с самого экрана, а не по снимку состояния.
    if s.short_of is not None or s.money is None or s.knowledge is None:
        return False
    if s.ticket_money is None or s.ticket_knowledge is None:
        return False
    return s.money >= s.ticket_money and s.knowledge >= s.ticket_knowledge


def _fight_outcome() -> Predicate:
    # «Ты ещё не встретил продавана» — правка встречи при любом исходе, её не ждём.
    base = expect_events(GorbushkaFight)

    def predicate(delivery: Delivery) -> Match | None:
        for event in delivery.events:
            if isinstance(event, GorbushkaNotice) and event.notice == "skills_changed":
                return Match(Verdict.REFUSED, event.notice)
        return base(delivery)

    return predicate


async def gorbushka(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    buy = bool(params.get("buy", False))
    async with ctx.lease("gorbushka"):
        screen, message = _gorbushka_screen(
            await ctx.send("/gorbushka", expect_events(GorbushkaScreen))
        )
        if screen.state == "need_ticket":
            if not buy:
                return ScenarioResult("nothing", "need_ticket")
            if not _affordable(screen):
                return ScenarioResult("nothing", "cant_afford")
            await ctx.safe_point()
            _require(
                await ctx.click(
                    message.msg_id, "gorbushka_new", expect_button("gorbushka_new_accept")
                )
            )
            await ctx.safe_point()
            screen, message = _gorbushka_screen(
                await ctx.click(
                    message.msg_id, "gorbushka_new_accept", expect_events(GorbushkaScreen)
                )
            )
            if screen.short_of is not None:
                return ScenarioResult("refused", "no_money")
        if screen.state != "meeting":
            return ScenarioResult("nothing", screen.state)
        await ctx.safe_point()
        return _finish(await ctx.click(message.msg_id, "gorbushka_fight", _fight_outcome()))


async def sleep(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    hours = int(params["hours"])
    async with ctx.lease("sleep"):
        menu = _require(await ctx.send("🛌Спать", expect_events(SleepMenu)))
        if menu.delivery is None:
            raise ScenarioStopped("unexpected_screen", menu)
        await ctx.safe_point()
        # Шаг выбора «отель/мост» (params["hotel"]) живьём не снят — сценарий не сертифицирован.
        return _finish(
            await ctx.click(menu.delivery.msg.msg_id, f"sleep_{hours}", expect_events(FellAsleep))
        )


async def run_scenario(
    fn: ScenarioFn, ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    try:
        return await fn(ctx, state, params)
    except ScenarioStopped as stop:
        if stop.result is not None and stop.result.step is Step.SUPPRESSED:
            return ScenarioResult("suppressed", stop.reason)
        if stop.result is not None and stop.result.step is Step.REFUSED:
            return ScenarioResult("refused", stop.reason)
        return ScenarioResult("stopped", stop.reason)
