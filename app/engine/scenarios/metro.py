from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import fields, replace
from datetime import datetime, timedelta
from typing import Any

from app.engine.bus import Delivery
from app.engine.events import Event, Unrecognized
from app.engine.gateway.types import Match, Predicate, Verdict
from app.engine.metro.budget import Budget, prior_step_s
from app.engine.metro.live import LiveFeed, live_frame
from app.engine.metro.solver import Click, Done, MetroSolver, Policy, policy_of
from app.engine.parsing.metro import (
    ENTRY_COST,
    WALL,
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
    MetroNoStamina,
    MetroNpc,
    recognize_metro,
)
from app.engine.parsing.profile import ProfileCompact
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
    MetroNoStamina,
)
ALERTS = {
    "late_risk": "metro: 85% of the time budget used and the exit is still unknown",
    "unknown_cell": "metro: unknown symbol in the map window, treated as an unknown cell",
}
# Продолжение застало ход («Идёшь …»): новое окно приходит через секунды — перечитать.
MOVING_WAIT_S = 10.0
MOVING_POLL_S = 2.0
# Зависание шага: на карте «Идёшь …» без новых правок дольше max(60 с, 3 × wait_s).
STUCK_WAIT_MIN_S = 60.0
STUCK_STEP_FACTOR = 3.0
MOVES = ("maze_up", "maze_down", "maze_left", "maze_right")
# «👍Выйти» — не раньше 1.5 с после правки с диалогом выхода (07.10 клик через 28 мс после неё
# дал итог «досрочно», а персонаж остался в метро).
EXIT_SETTLE = timedelta(seconds=1.5)
RELEASED = "metro: /main answered, out of the run"
_settle_sleep = asyncio.sleep
OPPOSITE: dict[str, tuple[str, tuple[int, int]]] = {
    "left": ("right", (2, 3)),
    "right": ("left", (2, 1)),
    "up": ("down", (3, 2)),
    "down": ("up", (1, 2)),
}


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
        if (probe := params.get("probe")) is not None:
            return await _probe(ctx, str(probe))
        if (resume := params.get("resume")) is not None:
            return await _resume(ctx, params, int(resume))
        # 🚇Метро — кнопка меню офиса: без безопасной точки после него.
        require(await ctx.send("🏢Офис", expect_events(InfoScreen, accept=_is_office)))
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


def _answered(step: StepResult) -> bool:
    """Игра ответила (профилем или отказом): персонаж вне метро — в забеге она молчит."""
    return step.step in (Step.OK, Step.REFUSED)


def _silent(step: StepResult) -> bool:
    return step.step is Step.FAILED and step.reason == "timeout"


async def _probe(ctx: ScenarioContext, probe: str) -> ScenarioResult:
    """Проверка выхода по решению планировщика (итог забега без подтверждённого выхода): `/main`
    или `/compact` перед битвой. Ответ снимает отметку забега в состоянии сам."""
    step = await ctx.send(f"/{probe}", expect_events(ProfileCompact))
    if _answered(step):
        if probe == "main":
            await ctx.notify(
                "info", "metro_main_released", "metro: /main answered, out of the run"
            )
        return ScenarioResult("done", "released")
    if not _silent(step):
        return finish(step)
    if probe == "compact":
        text = (
            "metro: no kick and no answer 10 min before the battle; "
            "sending nothing until the game answers"
        )
        await ctx.notify("error", "metro_stuck_unresolved", text)
    return ScenarioResult("nothing", "still_inside")


async def _exit_checked(
    ctx: ScenarioContext,
    result: ScenarioResult,
    solver: MetroSolver,
    record: Callable[[str], dict[str, Any]],
    *,
    anomaly: bool,
) -> ScenarioResult:
    """Итог забега ещё не выход: в забеге игра на команды не отвечает. `/compact`, без ответа —
    `/main` («К персонажу»), без ответа и на неё — персонаж застрял в метро до выброса."""
    if anomaly:
        text = "metro: the normal exit dialog was answered with the early-exit result"
        await ctx.notify("warn", "metro_exit_anomaly", text)
    compact = await ctx.send("/compact", expect_events(ProfileCompact))
    if _answered(compact):
        return result
    if not _silent(compact):
        return stopped(ScenarioStopped(compact.reason, compact), record(compact.reason))
    main = await ctx.send("/main", expect_events(ProfileCompact))
    if _answered(main):
        await ctx.notify("info", "metro_main_released", RELEASED)
        return result
    if not _silent(main):
        # Исход /main неизвестен: мог и вывести — застрявшим не считаем.
        return stopped(ScenarioStopped(main.reason, main), record(main.reason))
    kick = solver.budget.kick_at()
    when = kick.isoformat() if kick is not None else "unknown"
    text = (
        "metro: still inside after the finish, no answer to /compact and /main; "
        f"kick expected at {when}"
    )
    await ctx.notify("warn", "metro_stuck_after_exit", text)
    return ScenarioResult("stopped", "exit_unconfirmed", record("exit_unconfirmed"))


async def _settle(ctx: ScenarioContext, shown: IncomingMessage) -> None:
    left = (shown.date + EXIT_SETTLE - ctx.clock.now()).total_seconds()
    if left > 0:
        await _settle_sleep(left)


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


def _stuck_timeout_s(ctx: ScenarioContext, buffs: MetroBuffs) -> float:
    return max(STUCK_WAIT_MIN_S, STUCK_STEP_FACTOR * _wait_s(ctx, buffs))


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
    bought = [e for m in history for e in recognize_metro(m)[:1] if isinstance(e, MetroBuffs)]
    buffs = bought[-1] if bought else MetroBuffs(bought=(), offers=(), tokens=0, coins=0)
    stuck_s = _stuck_timeout_s(ctx, buffs)
    current = await ctx.reread(message)
    waited = 0.0
    moving: list[IncomingMessage] = []
    while (
        current is not None
        and _moving(current)
        and waited < MOVING_WAIT_S
        and not _stuck(ctx, current, stuck_s)
    ):
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
    if _moving(current) and not _stuck(ctx, current, stuck_s):
        return ScenarioResult("stopped", "resume_while_moving")
    # «Идёшь …», прочитанное при ожидании, — ход, которым пришли к текущему кадру; зависший
    # «Идёшь …» расшевеливает `_explore`.
    seen = [*history, *moving]
    frames = [(m, e) for m in seen if not _same(m, current) for e in recognize_metro(m)[:1]]
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


def _stuck(ctx: ScenarioContext, msg: IncomingMessage, after_s: float) -> bool:
    """«Идёшь …», которое игра не правит дольше срока зависания (по дате правки)."""
    return _moving(msg) and (ctx.clock.now() - msg.date).total_seconds() >= after_s


def _in_run(screen: Event | None) -> bool:
    """Кадр, с которого забег продолжается обычным путём."""
    return isinstance(screen, SCREENS) and not isinstance(screen, MetroEarlyExit | MetroFinished)


def _back_move(frame: IncomingMessage) -> str | None:
    """Шаг назад: кнопка, противоположная ходу из «Идёшь …», если клетка позади не стена."""
    screen = metro_screen(frame)
    if not isinstance(screen, MetroMap) or screen.direction not in OPPOSITE:
        return None
    back, (row, col) = OPPOSITE[screen.direction]
    window = screen.window
    if not (0 <= row < len(window) and 0 <= col < len(window[row])) or window[row][col] == WALL:
        return None
    return f"maze_{back}"


async def _recover_stuck(
    ctx: ScenarioContext,
    message: int,
    current: IncomingMessage,
    buffs: MetroBuffs,
    solver: MetroSolver,
    stages: list[str],
    record: Callable[[str], dict[str, Any]],
) -> ScenarioResult | IncomingMessage:
    """Лестница расшевеливания зависшего шага; каждая ступень — если предыдущая осталась без
    ответа: шаг назад → 🚪 и «Остаться» → 🚪 и «Выйти». Ответ, который не кадр забега и не
    ожидаемый экран (в том числе отказ игры), — остановка без выхода. Возвращает кадр, с
    которого забег продолжается; сработавшая ступень — в `stages`."""
    wait_s = _wait_s(ctx, buffs)
    tried: list[str] = []
    shown = current

    async def press(data: str) -> StepResult:
        return await ctx.click(
            message,
            data,
            expect_metro(message),
            revision=shown.revision,
            content=shown.content_hash(),
            timeout_s=wait_s,
        )

    async def unknown(screen: Event | None) -> ScenarioResult:
        kind = screen.kind if screen is not None else "unknown"
        text = f"metro: unexpected answer while recovering a stuck step ({kind}), not leaving"
        await ctx.notify("warn", "metro_stuck_unknown", text)
        return ScenarioResult("stopped", "stuck_unknown", record("stuck_unknown"))

    async def answer(step: StepResult) -> IncomingMessage | ScenarioResult | None:
        """Правка-ответ; None — ответа нет в срок."""
        if step.step is Step.OK:
            assert step.delivery is not None
            return step.delivery.msg
        if step.step is Step.FAILED and step.reason == "timeout":
            return None
        if step.step is Step.REFUSED:
            return await unknown(step.first(Refused) or step.first(Busy))
        require(step)
        raise AssertionError("unreachable")

    def recovered(frame: IncomingMessage, stage: str) -> IncomingMessage:
        stages.append(stage)
        return frame

    async def decline() -> IncomingMessage | ScenarioResult | None:
        """«Остаться» на показанном экране досрочного выхода."""
        await ctx.safe_point()
        got = await answer(await press("maze_exit_decline"))
        if got is None or isinstance(got, ScenarioResult):
            return got
        if _in_run(metro_screen(got)):
            return recovered(got, "exit_decline")
        return await unknown(metro_screen(got))

    async def late() -> IncomingMessage | ScenarioResult | None:
        """Правка, пришедшая после срока ступени: кадр забега — успех, экран досрочного
        выхода после двери — «Остаться», незнакомый экран — остановка; новое «Идёшь …» —
        зависание продолжается на нём."""
        nonlocal shown
        latest = ctx.latest(message)
        if latest is None or _same(latest, shown):
            return None
        if _moving(latest):
            shown = latest
            return None
        screen = metro_screen(latest)
        if _in_run(screen):
            return recovered(latest, tried[-1] if tried else "late_answer")
        if isinstance(screen, MetroEarlyExit) and tried[-1:] == ["exit_decline"]:
            shown = latest
            return await decline()
        return await unknown(screen)

    if (back := _back_move(current)) is not None:
        tried.append("back_step")
        await ctx.safe_point()
        got = await answer(await press(back))
        if got is not None:
            if isinstance(got, ScenarioResult):
                return got
            if _in_run(metro_screen(got)):
                return recovered(got, "back_step")
            return await unknown(metro_screen(got))

    await ctx.safe_point()
    if (got := await late()) is not None:
        return got
    tried.append("exit_decline")
    got = await answer(await press("maze_exit"))
    if isinstance(got, ScenarioResult):
        return got
    if got is not None:
        if not isinstance(metro_screen(got), MetroEarlyExit):
            return await unknown(metro_screen(got))
        shown = got
        if (got := await decline()) is not None:
            return got

    await ctx.safe_point()
    if (got := await late()) is not None:
        return got
    if not isinstance(metro_screen(shown), MetroEarlyExit):
        door = await press("maze_exit")
        if door.step is Step.REFUSED:
            return await unknown(door.first(Refused) or door.first(Busy))
        if door.step is not Step.OK:
            return await _halt(ctx, door.reason, record(door.reason))
        assert door.delivery is not None
        shown = door.delivery.msg
        if not isinstance(metro_screen(shown), MetroEarlyExit):
            return await unknown(metro_screen(shown))
        await ctx.safe_point()
    leave = await press("maze_exit_accept")
    if leave.step is Step.REFUSED:
        return await unknown(leave.first(Refused) or leave.first(Busy))
    if leave.step is not Step.OK:
        return await _halt(ctx, leave.reason, record(leave.reason))
    assert leave.delivery is not None
    finished = metro_screen(leave.delivery.msg)
    if not isinstance(finished, MetroFinished):
        return await unknown(finished)
    stages.append("stuck_exit")
    solver.observe(finished)
    text = f"metro: step stuck, left the run after trying: {', '.join(tried)}"
    await ctx.notify("warn", "metro_stuck_exit", text)
    return ScenarioResult("done", "finished", record("finished"))


async def _explore(
    ctx: ScenarioContext,
    message: int,
    current: IncomingMessage,
    buffs: MetroBuffs,
    solver: MetroSolver,
    started: datetime,
) -> ScenarioResult:
    """Обход с живым кадром для админки: на старте, после каждого экрана и в конце."""

    def frame(running: bool, outcome: str | None) -> dict[str, Any]:
        return live_frame(
            solver,
            message_id=message,
            scenario_run_id=ctx.run_id,
            started=started,
            now=ctx.clock.now(),
            running=running,
            outcome=outcome,
        )

    live = LiveFeed(ctx.publish, frame, ctx.clock.monotonic)
    live.update()
    outcome = "interrupted"
    try:
        result = await _walk(ctx, message, current, buffs, solver, started, live)
        outcome = result.reason
        return result
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        live.close(outcome)


async def _walk(
    ctx: ScenarioContext,
    message: int,
    current: IncomingMessage,
    buffs: MetroBuffs,
    solver: MetroSolver,
    started: datetime,
    live: LiveFeed,
) -> ScenarioResult:
    wait_s = _wait_s(ctx, buffs)
    stuck_s = _stuck_timeout_s(ctx, buffs)
    notified: set[str] = set(solver.alerts)
    # Сработавшие ступени лестницы зависшего шага — для статистики забегов.
    stages: list[str] = []

    def record(outcome: str) -> dict[str, Any]:
        finished = ctx.clock.now()
        run: dict[str, Any] = {
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
        if stages:
            run["stuck_recovered"] = list(stages)
        return {"metro": run}

    async def left(result: ScenarioResult, *, anomaly: bool = False) -> ScenarioResult:
        if (result.status, result.reason) != ("done", "finished"):
            return result
        return await _exit_checked(ctx, result, solver, record, anomaly=anomaly)

    shown: Event | None = None
    try:
        while True:
            if _stuck(ctx, current, stuck_s):
                # Продолжение застало давно зависший ход.
                recovered = await _recover_stuck(
                    ctx, message, current, buffs, solver, stages, record
                )
                if isinstance(recovered, ScenarioResult):
                    return await left(recovered)
                solver.cancel()
                solver.resync()
                current = recovered
                continue
            screen = metro_screen(current)
            if screen is None:
                return await _halt(ctx, "unexpected_screen", record("unexpected_screen"))
            before, shown = shown, screen
            move = solver.next(screen, ctx.clock.now())
            live.update()
            for alert in solver.alerts:
                if alert not in notified:
                    notified.add(alert)
                    await ctx.notify("warn", f"metro_{alert}", ALERTS.get(alert, alert))
            if isinstance(move, Done):
                # Итог «досрочно» в ответ на обычный диалог выхода, а не на 🚪: ошибка игры.
                anomaly = (
                    isinstance(screen, MetroFinished)
                    and screen.early
                    and isinstance(before, MetroExit)
                )
                return await left(
                    ScenarioResult("done", move.reason, record(move.reason)), anomaly=anomaly
                )
            if not isinstance(move, Click):
                return await _halt(ctx, move.reason, record(move.reason))
            if move.data == "maze_exit_accept" and isinstance(screen, MetroExit):
                await _settle(ctx, current)
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
            step = await ctx.click(
                message,
                move.data,
                expect_metro(message),
                revision=current.revision,
                content=current.content_hash(),
                timeout_s=stuck_s if move.data in MOVES else wait_s,
            )
            latest = ctx.latest(message)
            if (
                step.step is Step.FAILED
                and step.reason == "timeout"
                and latest is not None
                and _moving(latest)
            ):
                # Ход начался («Идёшь …»), а нового окна нет дольше срока зависания.
                recovered = await _recover_stuck(
                    ctx, message, latest, buffs, solver, stages, record
                )
                if isinstance(recovered, ScenarioResult):
                    return await left(recovered)
                solver.cancel()
                solver.resync()
                current = recovered
                continue
            require(step)
            assert step.delivery is not None
            current = step.delivery.msg
    except ScenarioStopped as stop:
        return stopped(stop, record(stop.reason))
