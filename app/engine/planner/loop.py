from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from app.engine.bus import Delivery
from app.engine.clock import Clock
from app.engine.gateway.gateway import ActionGateway
from app.engine.notify import NotifierPort
from app.engine.planner.decide import decide
from app.engine.planner.store import DecisionRecord, PlannerStore
from app.engine.planner.types import Act, Wait
from app.engine.scenarios.context import ScenarioContext
from app.engine.scenarios.library import ScenarioResult, run_scenario
from app.engine.scenarios.registry import CERTIFIED, SCENARIOS
from app.engine.settings import SettingsProvider
from app.engine.state.model import CharacterState

log = logging.getLogger(__name__)
RETRY_AFTER = timedelta(minutes=5)
# «Нечего делать» уже обновило состояние экраном; короткая пауза страхует от зацикливания.
NOTHING_RETRY = timedelta(minutes=1)
# Подавленное действие (dry_run) состояние не меняет: сценарий откладывается, решаются остальные.
SUPPRESSED_HOLD = timedelta(minutes=10)
DEEDS = tuple(name for name in SCENARIOS if name.startswith("deed:"))


class PlannerLoop:
    def __init__(
        self,
        *,
        gateway: ActionGateway,
        state: Callable[[], CharacterState],
        settings: SettingsProvider,
        clock: Clock,
        store: PlannerStore,
        notifier: NotifierPort,
        ready: Callable[[], str | None],
        poll_s: float = 5.0,
        max_idle_s: float = 1800.0,
        step_timeout_s: float = 20.0,
    ) -> None:
        self._gateway = gateway
        self._state = state
        self._settings = settings
        self._clock = clock
        self._store = store
        self._notifier = notifier
        self._ready = ready
        self._poll_s = poll_s
        self._max_idle_s = max_idle_s
        self._step_timeout_s = step_timeout_s
        self._wake = asyncio.Event()
        self._last_refresh: dict[str, datetime] = {}
        self._cooldowns: dict[str, datetime] = {}
        self._failing: set[str] = set()
        self._last_wait: DecisionRecord | None = None
        self.current: str | None = None
        self.next_wake: datetime | None = None

    async def on_delivery(self, delivery: Delivery) -> None:
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    async def run(self) -> None:
        while True:
            self._wake.clear()
            pause = await self.step()
            if pause is not None:
                await self._pause(pause)

    async def _pause(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), seconds)
        except TimeoutError:
            pass

    async def step(self) -> float | None:
        """Одно решение; возвращает, сколько ждать до следующего (None — сразу)."""
        now = self._clock.now()
        if self._ready() is not None:
            return self._poll_s
        settings = self._settings.current
        decision = decide(
            self._state(),
            settings,
            now,
            certified=CERTIFIED if settings.engine.mode == "live" else None,
            last_refresh=self._last_refresh,
            cooldowns=self._cooldowns,
        )
        if isinstance(decision, Wait):
            return await self._wait(now, decision)
        self._last_wait = None
        self.next_wake = None
        decision_id = await self._store.record(now, decision)
        await self._execute(decision, decision_id)
        return None

    async def _wait(self, now: datetime, decision: Wait) -> float:
        # Одинаковые ожидания подряд (пробуждение по каждому сообщению) журналим один раз.
        record = DecisionRecord.of(decision)
        if record != self._last_wait:
            await self._store.record(now, decision)
            self._last_wait = record
        self.next_wake = decision.until
        if decision.until is None:
            return self._max_idle_s
        return min(max((decision.until - now).total_seconds(), 0.0), self._max_idle_s)

    async def _execute(self, act: Act, decision_id: int) -> None:
        spec = SCENARIOS[act.scenario]
        settings = self._settings.current
        params = {**spec.params, **act.params}
        ctx = ScenarioContext(
            self._gateway,
            game_chat_id=settings.chats.game_chat_id,
            # Не зависит от режима: смена dry_run → live посреди сценария не делает шаг реальным.
            simulate=not spec.certified,
            paused=lambda: self._settings.current.engine.paused,
            timeout_s=self._step_timeout_s,
        )
        started = self._clock.now()
        run_id = await self._store.run_started(decision_id, act.scenario, params, started)
        self.current = act.scenario
        try:
            result = await run_scenario(spec.fn, ctx, self._state(), params)
        except Exception:
            log.exception("scenario %s crashed", act.scenario)
            result = ScenarioResult("failed", "crashed")
        finally:
            self.current = None
        finished = self._clock.now()
        await self._store.run_finished(run_id, result.status, result.reason, finished)
        await self._after(act, result, started, finished)

    async def _after(
        self, act: Act, result: ScenarioResult, started: datetime, finished: datetime
    ) -> None:
        name = act.scenario
        if name == "refresh":
            self._last_refresh[str(act.params["source"])] = started
        if result.status == "suppressed":
            # Подавленное дело означает «занят делом»: откладываются все дела.
            for held in DEEDS if name.startswith("deed:") else (name,):
                self._cooldowns[held] = finished + SUPPRESSED_HOLD
            return
        if result.status == "done" or result.reason == "paused":
            self._failing.discard(name)
            return
        retry = NOTHING_RETRY if result.status == "nothing" else RETRY_AFTER
        self._cooldowns[name] = finished + retry
        if result.status not in ("failed", "stopped") or name in self._failing:
            return
        # Одно уведомление на серию неудач сценария, до его следующего успеха.
        self._failing.add(name)
        await self._notifier.notify(
            "warn", "scenario_failed", f"{name}: {result.status} {result.reason}"
        )
