"""Запуск сбора артефакта: экран артефактов → экран старта → «👍Стартуем!»."""

from __future__ import annotations

from app.engine.parsing.artifacts import (
    MAX_LEVEL,
    ArtifactCollectStarted,
    ArtifactsScreen,
    ArtifactStartScreen,
)
from app.engine.scenarios.context import (
    ScenarioContext,
    ScenarioStopped,
    Step,
    expect_events,
)
from app.engine.scenarios.library import (
    Params,
    ScenarioResult,
    finish,
    require,
    wrong_screen,
)
from app.engine.state.model import CharacterState

SCREEN = "/artefacts"


async def artifact_start(
    ctx: ScenarioContext, state: CharacterState, params: Params
) -> ScenarioResult:
    """`/artr_<x>` игра принимает только сразу после экрана артефактов — пара под арендой, без
    безопасной точки. Клик «Стартуем!» шлюз пропускает, пока запись сбора ждёт запуска этого
    артефакта; неясный исход клика сверяется экраном артефактов."""
    artifact = str(params["artifact"])
    async with ctx.lease("artifact_start"):
        screen = require(await ctx.send(SCREEN, expect_events(ArtifactsScreen))).first(
            ArtifactsScreen
        )
        if screen is None:
            raise ScenarioStopped("unexpected_screen")
        if screen.collecting is not None:
            return ScenarioResult("nothing", "already_collecting")
        if artifact not in screen.recollect or screen.levels.get(artifact) == MAX_LEVEL:
            return ScenarioResult("nothing", "not_recollectable")
        opened = await ctx.send(
            f"/artr_{artifact}",
            expect_events(
                ArtifactStartScreen,
                accept=lambda e: isinstance(e, ArtifactStartScreen) and e.artifact == artifact,
            ),
        )
        if opened.step is Step.REFUSED and opened.reason == "artifact_max":
            return ScenarioResult("nothing", "not_recollectable")
        if opened.step is not Step.OK or opened.delivery is None:
            return wrong_screen(opened)
        await ctx.safe_point()
        clicked = await ctx.click(
            opened.delivery.msg.msg_id,
            f"artr_{artifact}_accept",
            expect_events(
                ArtifactCollectStarted,
                accept=lambda e: isinstance(e, ArtifactCollectStarted) and e.artifact == artifact,
            ),
        )
    started = clicked.first(ArtifactCollectStarted)
    if clicked.step is Step.OK and started is not None and clicked.delivery is not None:
        details = {
            "artifact": artifact,
            "started_at": clicked.delivery.msg.date.isoformat(),
            "deed": started.deed,
        }
        return ScenarioResult("done", "started", details)
    if clicked.step is not Step.FAILED:
        return finish(clicked)
    return await _checked(ctx, artifact, clicked.reason)


async def _checked(ctx: ScenarioContext, artifact: str, reason: str) -> ScenarioResult:
    step = await ctx.send(SCREEN, expect_events(ArtifactsScreen))
    screen = step.first(ArtifactsScreen)
    if step.step is Step.OK and screen is not None and screen.collecting == artifact:
        return ScenarioResult("done", "started_checked")
    return ScenarioResult("failed", reason)
