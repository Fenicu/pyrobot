from __future__ import annotations

import asyncio
from dataclasses import fields, replace
from datetime import datetime, timedelta
from typing import Any

from app.engine.bus import Delivery
from app.engine.events import Event, Unrecognized
from app.engine.gateway.types import Match, Predicate, Verdict
from app.engine.metro.budget import Budget, prior_step_s
from app.engine.metro.solver import Click, Done, MetroSolver, Policy, policy_of
from app.engine.parsing.metro import (
    ENTRY_COST,
    MetroBuffs,
    MetroChest,
    MetroChestOpened,
    MetroEarlyExit,
    MetroEntrance,
    MetroExit,
    MetroFight,
    MetroFinished,
    MetroFirstAid,
    MetroLoot,
    MetroMap,
    MetroNpc,
    recognize_metro,
)
from app.engine.parsing.refusals import Busy, Refused
from app.engine.parsing.screens import InfoScreen
from app.engine.scenarios.context import (
    ScenarioContext,
    ScenarioStopped,
    Step,
    StepResult,
    expect_events,
)
from app.engine.scenarios.library import Params, ScenarioResult, finish, require, stopped
from app.engine.settings import MetroSection
from app.engine.state.model import CharacterState
from app.engine.types import IncomingMessage

# Параметры, не заданные запуском, — из настроек `metro` по умолчанию.
DEFAULTS = MetroSection()
SCREENS = (
    MetroMap,
    MetroLoot,
    MetroNpc,
    MetroFight,
    MetroChest,
    MetroChestOpened,
    MetroFirstAid,
    MetroExit,
    MetroEarlyExit,
    MetroFinished,
)
ALERTS = {
    "late_risk": "metro: 85% of the time budget used and the exit is still unknown",
    "unknown_cell": "metro: unknown symbol in the map window, treated as an unknown cell",
}
# Продолжение застало ход («Идёшь …»): новое окно приходит через секунды — перечитать.
MOVING_WAIT_S = 10.0
MOVING_POLL_S = 2.0


def expect_metro(message_id: int) -> Predicate:
    """Ход подтверждает новая правка сообщения забега: не «Идёшь …» со старым окном.

    Любая другая правка тоже подтверждает клик (он сработал): незнакомый экран сценарий
    останавливает сам, не дожидаясь таймаута. Отказ игры отдельным сообщением — отказ шага.
    """

    def predicate(delivery: Delivery) -> Match | None:
        if delivery.msg.msg_id != message_id:
            refusal = next((e for e in delivery.events if isinstance(e, Refused | Busy)), None)
            if refusal is None:
                return None
            return Match(Verdict.REFUSED, str(getattr(refusal, "reason", None) or refusal.kind))
        if any(isinstance(e, MetroMap) and e.footer == "going" for e in delivery.events):
            return None
        known = (*SCREENS, MetroBuffs)
        screen = next((e for e in delivery.events if isinstance(e, known)), None)
        return Match(Verdict.CONFIRMED, screen.kind if screen is not None else "other_screen")

    return predicate


def metro_screen(msg: IncomingMessage) -> Event | None:
    return next((e for e in recognize_metro(msg) if isinstance(e, SCREENS)), None)


def _policy(params: Params) -> Policy:
    base = policy_of(DEFAULTS)
    given = {
        f.name: type(getattr(base, f.name))(params[f.name])
        for f in fields(Policy)
        if f.name in params
    }
    return replace(base, **given)


def _is_office(event: Event) -> bool:
    return isinstance(event, InfoScreen) and event.name == "office"


class Halted(Exception):
    """Незнакомый экран или сбой хода: остановка с уведомлением `metro_halted`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def _halt(
    ctx: ScenarioContext, reason: str, details: dict[str, Any] | None = None
) -> ScenarioResult:
    text = f"metro: stopped ({reason}); the planner will try to resume the run"
    await ctx.notify("warn", "metro_halted", text)
    return ScenarioResult("stopped", reason, details)


def _buffs_of(step: StepResult) -> MetroBuffs:
    # Любая правка сообщения подтверждает клик; тип экрана разбирается явно.
    buffs = step.first(MetroBuffs)
    if buffs is None:
        raise Halted("unexpected_screen")
    return buffs


def _entrance_answer() -> Predicate:
    """Ответ на `🚇Метро` разбирается явно: вход, отказ (кулдаун) или незнакомый экран."""

    def predicate(delivery: Delivery) -> Match | None:
        for event in delivery.events:
            if isinstance(event, MetroEntrance):
                return Match(Verdict.CONFIRMED, event.kind)
            if isinstance(event, Refused | Busy):
                return Match(Verdict.REFUSED, str(getattr(event, "reason", None) or event.kind))
            if isinstance(event, Unrecognized):
                return Match(Verdict.CONFIRMED, "other_screen")
        return None

    return predicate


async def metro(ctx: ScenarioContext, state: CharacterState, params: Params) -> ScenarioResult:
    """🏢Офис → 🚇Метро → вход → бафы за 🕳 → старт → обход решателем → выход.

    С `params["resume"]` (id сообщения забега) — продолжение после рестарта или остановки.
    """
    async with ctx.lease("metro"):
        if (resume := params.get("resume")) is not None:
            return await _resume(ctx, params, int(resume))
        require(await ctx.send("🏢Офис", expect_events(InfoScreen, accept=_is_office)))
        await ctx.safe_point()
        entrance = await ctx.send("🚇Метро", _entrance_answer())
        if entrance.step is Step.REFUSED:
            return finish(entrance)
        shown = require(entrance).delivery
        offer = entrance.first(MetroEntrance)
        if shown is None or offer is None:
            return await _halt(ctx, "unexpected_screen")
        if offer.cost != ENTRY_COST or offer.motivation < offer.cost:
            return await _decline(ctx, shown.msg, offer)
        try:
            await ctx.safe_point()
            opened = await _click_on(ctx, shown.msg, "maze_enter_accept")
            assert opened.delivery is not None
            return await _buy_and_start(ctx, params, opened.delivery.msg, _buffs_of(opened))
        except Halted as halted:
            return await _halt(ctx, halted.reason)


async def _decline(
    ctx: ScenarioContext, shown: IncomingMessage, offer: MetroEntrance
) -> ScenarioResult:
    """Вход стоит не столько, сколько закладывает планировщик, или 🔥 не хватает: «Вхожу» не
    нажимаем. Ответ на отказ живьём не видели — итог от него не зависит."""
    reason = "entry_cost_changed" if offer.cost != ENTRY_COST else "no_motivation"
    if reason == "entry_cost_changed":
        text = f"metro: entry costs {offer.cost} motivation instead of {ENTRY_COST}, not entering"
        await ctx.notify("warn", "metro_entry_cost", text)
    await ctx.safe_point()
    message = shown.msg_id
    await ctx.click(
        message,
        "maze_enter_decline",
        expect_metro(message),
        revision=shown.revision,
        content=shown.content_hash(),
    )
    return ScenarioResult("nothing", reason)


async def _click_on(
    ctx: ScenarioContext,
    shown: IncomingMessage,
    data: str,
    timeout_s: float | None = None,
) -> StepResult:
    """Клик по кадру `shown`: если после безопасной точки сообщение уже другое — остановка;
    шлюз ещё раз сверит ревизию и хеш содержимого перед отправкой."""
    message = shown.msg_id
    latest = ctx.latest(message)
    if latest is not None and not _same(latest, shown):
        raise Halted("screen_changed")
    return require(
        await ctx.click(
            message,
            data,
            expect_metro(message),
            revision=shown.revision,
            content=shown.content_hash(),
            timeout_s=timeout_s,
        )
    )


async def _buy_and_start(
    ctx: ScenarioContext, params: Params, shown: IncomingMessage, buffs: MetroBuffs
) -> ScenarioResult:
    message = shown.msg_id
    try:
        for name in (str(b) for b in params.get("buffs", DEFAULTS.buffs)):
            if name not in buffs.offers:
                continue
            if buffs.token_price is not None and buffs.tokens < buffs.token_price:
                break
            await ctx.safe_point()
            step = await _click_on(ctx, shown, f"maze_buf_tokens_{name}")
            assert step.delivery is not None
            shown, buffs = step.delivery.msg, _buffs_of(step)
            if name not in buffs.bought:
                await ctx.notify("warn", "metro_buff_not_bought", f"metro: buff {name} not bought")
        if "fastMove" not in buffs.bought:
            await ctx.notify("warn", "metro_slow", "metro: no fast move buff, a step takes 20 s")
        await ctx.safe_point()
        first = await _click_on(ctx, shown, "maze_start", _wait_s(ctx, buffs))
    except Halted as halted:
        return await _halt(ctx, halted.reason)
    assert first.delivery is not None
    started = ctx.clock.now()
    solver = MetroSolver(_policy(params), _budget(params, started, buffs))
    return await _explore(ctx, message, first.delivery.msg, buffs, solver, started)


def _wait_s(ctx: ScenarioContext, buffs: MetroBuffs) -> float:
    # Без быстрого шага переход идёт 20 с вместо 5: ожидание кадра растёт так же.
    return ctx.timeout_s * prior_step_s("fastMove" in buffs.bought) / prior_step_s(True)


def _budget(params: Params, started: datetime, buffs: MetroBuffs) -> Budget:
    battle = params.get("battle_at")
    return Budget(
        started=started,
        battle_at=datetime.fromisoformat(str(battle)) if battle else None,
        margin=timedelta(minutes=float(params.get("margin_min", DEFAULTS.margin_min))),
        # Время шага — по фактически купленному бафу.
        step_prior_s=prior_step_s("fastMove" in buffs.bought),
    )


def _same(a: IncomingMessage, b: IncomingMessage) -> bool:
    return a.revision == b.revision and a.content_hash() == b.content_hash()


async def _resume(ctx: ScenarioContext, params: Params, message: int) -> ScenarioResult:
    """Продолжение забега. Карту и позицию восстанавливают правки сообщения из журнала, а решение
    принимается только по текущему экрану, прочитанному из Telegram: клик до рестарта мог дойти
    до игры, а его правка — не до журнала; повторять его по старому кадру нельзя."""
    history = await ctx.history(message)
    if not history:
        return ScenarioResult("stopped", "resume_without_history")
    current = await ctx.reread(message)
    waited = 0.0
    moving: list[IncomingMessage] = []
    while current is not None and _moving(current) and waited < MOVING_WAIT_S:
        moving.append(current)
        await asyncio.sleep(MOVING_POLL_S)
        waited += MOVING_POLL_S
        current = await ctx.reread(message)
    if current is None:
        return await _halt(ctx, "resume_unreadable")
    shown = recognize_metro(current)
    if shown and isinstance(shown[0], MetroBuffs):
        return await _buy_and_start(ctx, params, current, shown[0])
    screen = metro_screen(current)
    if screen is None:
        # Последний экран незнакомый: без человека не продолжаем.
        return await _halt(ctx, "resume_unknown_screen")
    if _moving(current):
        return ScenarioResult("stopped", "resume_while_moving")
    # «Идёшь …», прочитанное при ожидании, — ход, которым пришли к текущему кадру.
    seen = [*history, *moving]
    frames = [(m, e) for m in seen if not _same(m, current) for e in recognize_metro(m)[:1]]
    bought = [e for _, e in frames if isinstance(e, MetroBuffs)]
    buffs = bought[-1] if bought else MetroBuffs(bought=(), offers=(), tokens=0, coins=0)
    maze = [n for n, (_, e) in enumerate(frames) if isinstance(e, SCREENS)]
    started = frames[maze[0]][0].date if maze else current.date
    solver = MetroSolver(_policy(params), _budget(params, started, buffs))
    for msg, event in frames[maze[0] :] if maze else ():
        if isinstance(event, SCREENS):
            solver.replay(event, msg.date)
    solver.update_mode(ctx.clock.now())
    solver.resync()
    return await _explore(ctx, message, current, buffs, solver, started)


def _moving(msg: IncomingMessage) -> bool:
    screen = metro_screen(msg)
    return isinstance(screen, MetroMap) and screen.footer == "going"


async def _explore(
    ctx: ScenarioContext,
    message: int,
    current: IncomingMessage,
    buffs: MetroBuffs,
    solver: MetroSolver,
    started: datetime,
) -> ScenarioResult:
    wait_s = _wait_s(ctx, buffs)
    notified: set[str] = set(solver.alerts)

    def record(outcome: str) -> dict[str, Any]:
        finished = ctx.clock.now()
        return {
            "metro": {
                **solver.snapshot(),
                "message_id": message,
                "buffs": list(buffs.bought),
                "tokens": buffs.tokens,
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "duration_s": (finished - started).total_seconds(),
                "step_s": solver.step_s(),
                "result": solver.result,
                "outcome": outcome,
            }
        }

    try:
        while True:
            screen = metro_screen(current)
            if screen is None:
                return await _halt(ctx, "unexpected_screen", record("unexpected_screen"))
            move = solver.next(screen, ctx.clock.now())
            for alert in solver.alerts:
                if alert not in notified:
                    notified.add(alert)
                    await ctx.notify("warn", f"metro_{alert}", ALERTS.get(alert, alert))
            if isinstance(move, Done):
                return ScenarioResult("done", move.reason, record(move.reason))
            if not isinstance(move, Click):
                return await _halt(ctx, move.reason, record(move.reason))
            await ctx.safe_point()
            latest = ctx.latest(message)
            if latest is not None and not _same(latest, current):
                # За безопасной точкой экран сменился (ручное действие): решение пересчитывается
                # по новому кадру; не кадр карты — позицию не восстановить, остановка.
                solver.cancel()
                if not isinstance(metro_screen(latest), MetroMap):
                    return await _halt(ctx, "screen_changed", record("screen_changed"))
                current = latest
                continue
            step = require(
                await ctx.click(
                    message,
                    move.data,
                    expect_metro(message),
                    revision=current.revision,
                    content=current.content_hash(),
                    timeout_s=wait_s,
                )
            )
            assert step.delivery is not None
            current = step.delivery.msg
    except ScenarioStopped as stop:
        return stopped(stop, record(stop.reason))
