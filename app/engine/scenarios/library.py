from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.engine.parsing.activities import ActivityStarted
from app.engine.parsing.items import BookRead, CardUsed, ContainerOpened, PrizeboxOpened
from app.engine.reconcile import FOOD, GIFTS, GORBUSHKA, INVENTORY, PROFILE
from app.engine.scenarios.context import (
    ScenarioContext,
    ScenarioStopped,
    Step,
    StepResult,
    expect_events,
)
from app.engine.state.model import CharacterState

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
REFRESH = {s.name: s for s in (PROFILE, INVENTORY, FOOD, GIFTS, GORBUSHKA)}


_STATUS: dict[Step, Status] = {
    Step.OK: "done",
    Step.REFUSED: "refused",
    Step.SUPPRESSED: "suppressed",
    Step.FAILED: "failed",
}


def _finish(step: StepResult) -> ScenarioResult:
    return ScenarioResult(_STATUS[step.step], step.reason)


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
