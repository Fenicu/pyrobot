from __future__ import annotations

from datetime import date

from app.engine.gametime import tasks_day
from app.engine.parsing.crew import CrewScreen
from app.engine.parsing.daily import DailyTasksScreen, TaskChosen, TaskConfirm
from app.engine.scenarios.context import ScenarioContext, Step, StepResult, expect_events
from app.engine.scenarios.library import Params, ScenarioResult, finish
from app.engine.state.model import CharacterState

CREW = "/crew"
TASKS = "⏳Задания"


def _failed(step: StepResult) -> ScenarioResult:
    # «Если жаждешь общения…» — команда ушла не с того экрана (игрок мог листать меню с телефона).
    # Это неудача, а не отказ игры: пауза повтора растёт, об этом уведомляют.
    if step.step is Step.REFUSED and step.reason == "unknown_command":
        return ScenarioResult("failed", "wrong_screen")
    return finish(step)


async def _open(ctx: ScenarioContext) -> tuple[DailyTasksScreen, date] | ScenarioResult:
    """Экран заданий и его день: `⏳Задания` работает только из меню команды. Безопасной точки
    между шагами нет: срочное действие между ними сбило бы экран."""
    crew = await ctx.send(CREW, expect_events(CrewScreen))
    if crew.step is not Step.OK:
        return _failed(crew)
    opened = await ctx.send(TASKS, expect_events(DailyTasksScreen))
    screen = opened.first(DailyTasksScreen)
    if opened.step is not Step.OK or screen is None or opened.delivery is None:
        return _failed(opened)
    return screen, tasks_day(opened.delivery.msg.date)


def _day_changed(ctx: ScenarioContext, day: date) -> bool:
    # Сброс в 00:00 MSK между шагами: вариант со вчерашнего экрана может уже не существовать.
    return tasks_day(ctx.clock.now()) != day


async def daily_refresh(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    async with ctx.lease("daily_tasks"):
        opened = await _open(ctx)
    return opened if isinstance(opened, ScenarioResult) else ScenarioResult("done", "screen")


async def daily_pick(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    """Выбор личного задания `task` (например, `convDets_hard`). Начинается с чтения экрана,
    поэтому повтор после рестарта или неизвестного исхода безопасен — `already_chosen`."""
    task = str(params["task"])
    async with ctx.lease("daily_tasks"):
        opened = await _open(ctx)
        if isinstance(opened, ScenarioResult):
            return opened
        screen, day = opened
        if screen.chosen is not None:
            return ScenarioResult("nothing", "already_chosen")
        if not any(offer.command == f"/t_{task}" for offer in screen.offers):
            return ScenarioResult("nothing", "offer_gone")
        confirm = expect_events(
            TaskConfirm, accept=lambda e: isinstance(e, TaskConfirm) and e.task == task
        )
        if _day_changed(ctx, day):
            return ScenarioResult("nothing", "day_changed")
        asked = await ctx.send(f"/t_{task}", confirm)
        if asked.step is not Step.OK or asked.delivery is None:
            return _failed(asked)
        if _day_changed(ctx, day):
            return ScenarioResult("nothing", "day_changed")
        # Кнопка привязана к сообщению подтверждения, а не к экрану.
        chosen = await ctx.click(
            asked.delivery.msg.msg_id, f"t_{task}_confirm", expect_events(TaskChosen)
        )
        return finish(chosen)
