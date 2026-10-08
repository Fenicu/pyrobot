from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.engine.bus import Delivery
from app.engine.commands import TANGERINE_GIFT_SHOP
from app.engine.events import Event
from app.engine.gateway.types import Match, Verdict
from app.engine.parsing.activities import ActivityStarted
from app.engine.parsing.food import FastfoodEaten, FoodMenu
from app.engine.parsing.gifts import (
    TANGERINE_GIFT_PRICE,
    TangerineGiftBought,
    TangerineGiftConfirm,
    TangerineGiftOpened,
    TangerineGiftShop,
)
from app.engine.parsing.gorbushka import GorbushkaFight, GorbushkaNotice, GorbushkaScreen
from app.engine.parsing.items import (
    BookRead,
    CardUsed,
    ContainerOpened,
    GiftsScreen,
    Inventory,
    PrizeboxOpened,
)
from app.engine.parsing.levelup import LevelUpStep
from app.engine.parsing.refusals import Refused
from app.engine.parsing.sleep import FellAsleep, SleepMenu, SleepPlace
from app.engine.reconcile import (
    ARTIFACTS,
    FOOD,
    GIFTS,
    GORBUSHKA,
    INVENTORY,
    PROFILE,
    STARTUP,
    STOCKS,
    UPGRADES,
)
from app.engine.scenarios.context import (
    Predicate,
    ScenarioContext,
    ScenarioStopped,
    Step,
    StepResult,
    expect_button,
    expect_edit,
    expect_events,
)
from app.engine.state.model import CharacterState
from app.engine.types import IncomingMessage

Status = Literal["done", "nothing", "refused", "suppressed", "failed", "stopped"]


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    status: Status
    reason: str = ""
    # Подробности для журнала (у метро — запись забега).
    details: dict[str, Any] | None = None


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
    "startup": "/dos",
}
FOOD_BUTTONS = {
    "hotdog": "🌭Хот-дог",
    "pizza": "🍕Пицца",
    "burger": "🍔Бургер",
    "banana": "🍌Банан",
}
REFRESH = {
    s.name: s
    for s in (PROFILE, INVENTORY, FOOD, GIFTS, GORBUSHKA, ARTIFACTS, UPGRADES, STOCKS, STARTUP)
}


_STATUS: dict[Step, Status] = {
    Step.OK: "done",
    Step.REFUSED: "refused",
    Step.SUPPRESSED: "suppressed",
    Step.FAILED: "failed",
}


def finish(step: StepResult) -> ScenarioResult:
    return ScenarioResult(_STATUS[step.step], step.reason)


# Меню команды: `⏳Задания` и `/crew_factory` игра принимает только из него.
CREW = "/crew"


def wrong_screen(step: StepResult) -> ScenarioResult:
    """Итог неудачного шага экранной команды. «Если жаждешь общения…» — команда ушла не с того
    экрана (игрок мог листать меню с телефона): это неудача, а не отказ игры — пауза повтора
    растёт, об этом уведомляют."""
    if step.step is Step.REFUSED and step.reason == "unknown_command":
        return ScenarioResult("failed", "wrong_screen")
    return finish(step)


def require(step: StepResult) -> StepResult:
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
    return finish(step)


_SIMPLE: dict[str, tuple[str, type[Event]]] = {
    "book": ("/read_exp", BookRead),
    "card": ("/use_card", CardUsed),
}
_CONTAINERS = {
    "container_small": "/unbox_ls",
    "container_medium": "/unbox_lm",
}


async def free_item(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    item = str(params["item"])
    if item in _SIMPLE:
        command, event = _SIMPLE[item]
        return finish(await ctx.send(command, expect_events(event)))
    if item == "prizebox":
        return await _open_prizebox(ctx)
    return await _open_container(ctx, item)


async def _open_prizebox(ctx: ScenarioContext) -> ScenarioResult:
    # Игра принимает /unbox только с экрана рюкзака, иначе общая справка «Если жаждешь общения…»:
    # безопасной точки между ними нет — ручное или срочное действие сбило бы экран.
    async with ctx.lease("free_item"):
        inv = require(await ctx.send(INVENTORY.command, expect_events(Inventory))).first(Inventory)
        if inv is None:
            raise ScenarioStopped("unexpected_screen")
        if not inv.prizebox:
            return ScenarioResult("nothing", "no_prizebox")
        if (inv.prizebox_in_s or 0) > 0:
            return ScenarioResult("nothing", "prizebox_locked")
        return finish(await ctx.send("/unbox", expect_events(PrizeboxOpened)))


async def _open_container(ctx: ScenarioContext, item: str) -> ScenarioResult:
    # Игра принимает /unbox_ls и /unbox_lm только с экрана подарков: без безопасной точки.
    command = _CONTAINERS[item]
    async with ctx.lease("free_item"):
        gifts = require(await ctx.send(GIFTS.command, expect_events(GiftsScreen))).first(
            GiftsScreen
        )
        if gifts is None:
            raise ScenarioStopped("unexpected_screen")
        count = gifts.containers_small if item == "container_small" else gifts.containers_medium
        if count == 0:
            return ScenarioResult("nothing", "no_containers")
        return finish(await ctx.send(command, expect_events(ContainerOpened)))


# За запуск открывается не больше 10 подарков за 🍊 — остальные следующим.
TANGERINE_GIFTS_BATCH = 10


async def tangerine_gifts(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    """/gifts → при 🍊 на подарок «🎁 за 10🍊» и самый большой вариант количества → /unbox_t по
    одному. `open` = False (персонаж занят, открытие игра не даст) — только покупка. Покупка
    и первое открытие идут подряд с экрана подарков; безопасные точки — только между открытиями."""
    async with ctx.lease("tangerine_gifts"):
        screen = require(await ctx.send(GIFTS.command, expect_events(GiftsScreen))).first(
            GiftsScreen
        )
        if screen is None:
            raise ScenarioStopped("unexpected_screen")
        have, bought = screen.tangerine_gifts or 0, 0
        if (screen.tangerines or 0) >= TANGERINE_GIFT_PRICE:
            have, bought = await _buy_tangerine_gifts(ctx)
        if have <= 0 or not params.get("open", True):
            if bought:
                return ScenarioResult("done")
            return ScenarioResult("nothing", "no_gifts" if have <= 0 else "busy")
        for opened in range(min(have, TANGERINE_GIFTS_BATCH)):
            if opened:
                await ctx.safe_point()
            step = await ctx.send("/unbox_t", expect_events(TangerineGiftOpened))
            if step.step is not Step.OK:
                # Купленное или открытое — уже итог запуска; остальное откроет следующий.
                return ScenarioResult("done", step.reason) if bought or opened else finish(step)
        return ScenarioResult("done")


async def _buy_tangerine_gifts(ctx: ScenarioContext) -> tuple[int, int]:
    """Экран покупки и клик по самому большому варианту (на сколько хватает 🍊), затем
    «👍Покупаю!» с правки-подтверждения (игра без него — покупка сразу): подарков после покупки и
    сколько куплено. Кнопок нет (🍊 не хватает) — без покупки."""
    step = require(await ctx.send(TANGERINE_GIFT_SHOP, expect_events(TangerineGiftShop)))
    shop = step.first(TangerineGiftShop)
    if shop is None or step.delivery is None:
        raise ScenarioStopped("unexpected_screen", step)
    if not shop.options:
        return shop.gifts, 0
    msg, count = step.delivery.msg, shop.options[-1]
    data = f"g_tangerines_small_{count}"
    clicked = require(
        await ctx.click(
            msg.msg_id,
            data,
            expect_edit(msg.msg_id, TangerineGiftBought, TangerineGiftConfirm),
            msg.revision,
            content=msg.content_hash(),
        )
    )
    if (asked := clicked.first(TangerineGiftConfirm)) is not None and clicked.delivery is not None:
        if asked.count != count:
            raise ScenarioStopped("unexpected_screen", clicked)
        confirm = clicked.delivery.msg
        clicked = require(
            await ctx.click(
                msg.msg_id,
                f"{data}_accept",
                expect_edit(msg.msg_id, TangerineGiftBought),
                confirm.revision,
                content=confirm.content_hash(),
            )
        )
    bought, after = clicked.first(TangerineGiftBought), clicked.first(TangerineGiftShop)
    if bought is None or after is None:
        raise ScenarioStopped("unexpected_screen", clicked)
    return after.gifts, bought.count


async def refresh(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    source = REFRESH[str(params["source"])]
    return finish(await ctx.send(source.command, expect_events(source.event)))


async def fastfood(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    # Кнопки еды — из меню 🍴, поэтому сначала открываем меню (nav), и без безопасной точки.
    async with ctx.lease("fastfood"):
        menu = require(await ctx.send("/to_eat", expect_events(FoodMenu))).first(FoodMenu)
        # Кулдаун виден в самом меню: кнопка дала бы только отказ.
        if menu is not None and (menu.fastfood_in_s or 0) > 0:
            return ScenarioResult("nothing", "fastfood_cooldown")
        return finish(
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

    # Кнопки навыков — из меню level-up, каждая следующая — с экрана после предыдущей: безопасных
    # точек между шагами нет.
    async with ctx.lease("levelup"):
        require(await ctx.send("/levelup", step("menu")))
        require(await ctx.send(main, step("main_skill")))
        return finish(await ctx.send(extra, step("done")))


def _gorbushka_screen(step: StepResult) -> tuple[GorbushkaScreen, IncomingMessage]:
    screen = require(step).first(GorbushkaScreen)
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
            require(
                await ctx.click(
                    message.msg_id,
                    "gorbushka_new",
                    expect_button("gorbushka_new_accept", refuse=(Refused,)),
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
        return finish(await ctx.click(message.msg_id, "gorbushka_fight", _fight_outcome()))


def _asleep(where: str, hours: int) -> Predicate:
    """Итог сна сверяется с выбором: место, часы, не принудительный. Меню с «❌Тебе не хватает
    ещё N 💵» вместо сна — отказ по деньгам."""

    def predicate(delivery: Delivery) -> Match | None:
        for event in delivery.events:
            if isinstance(event, SleepMenu) and event.short_of is not None:
                return Match(Verdict.REFUSED, "no_money")
            if isinstance(event, FellAsleep):
                if (event.where, event.hours, event.forced) != (where, hours, False):
                    return Match(Verdict.REFUSED, "place_mismatch")
                return Match(Verdict.CONFIRMED, event.kind)
        return None

    return predicate


def _prefer_hotel(state: CharacterState, params: Params, place: SleepPlace) -> bool:
    """Место — по цене отеля с экрана выбора и деньгам: отель, если после резерва билета
    Горбушки денег не меньше max(цена, порог); `params.hotel` — явное указание."""
    if (explicit := params.get("hotel")) is not None:
        return bool(explicit)
    money = state.money.value if state.money is not None else None
    if money is None:
        return False
    threshold = params.get("hotel_threshold")
    need = max(place.hotel_cost, int(threshold)) if threshold is not None else place.hotel_cost
    return money - int(params.get("ticket_reserve", 0)) >= need


async def sleep(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    """`🛌Спать` → часы (`sleep_N`) → место (`sleep_Hotel` / `sleep_Bridge`) → засыпание."""
    hours = int(params["hours"])
    async with ctx.lease("sleep"):
        menu = require(await ctx.send("🛌Спать", expect_events(SleepMenu)))
        if menu.delivery is None:
            raise ScenarioStopped("unexpected_screen", menu)
        message = menu.delivery.msg.msg_id
        await ctx.safe_point()
        chosen = expect_events(
            SleepPlace, accept=lambda e: isinstance(e, SleepPlace) and e.hours == hours
        )
        place = require(await ctx.click(message, f"sleep_{hours}", chosen)).first(SleepPlace)
        if place is None:
            raise ScenarioStopped("unexpected_screen")
        where = "hotel" if _prefer_hotel(state, params, place) else "bridge"
        await ctx.safe_point()
        step = await ctx.click(message, f"sleep_{where.capitalize()}", _asleep(where, hours))
        got = step.first(FellAsleep)
        if step.step is Step.REFUSED and step.reason == "place_mismatch" and got is not None:
            forced = " forced" if got.forced else ""
            text = f"sleep: asked {where} {hours}h, got {got.where} {got.hours}h{forced}"
            await ctx.notify("warn", "sleep_place_mismatch", text)
        return finish(step)


def stopped(stop: ScenarioStopped, details: dict[str, Any] | None = None) -> ScenarioResult:
    if stop.result is not None and stop.result.step is Step.SUPPRESSED:
        return ScenarioResult("suppressed", stop.reason, details)
    if stop.result is not None and stop.result.step is Step.REFUSED:
        return ScenarioResult("refused", stop.reason, details)
    return ScenarioResult("stopped", stop.reason, details)


async def run_scenario(
    fn: ScenarioFn, ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    try:
        return await fn(ctx, state, params)
    except ScenarioStopped as stop:
        return stopped(stop)
