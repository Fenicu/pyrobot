from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from app.engine.bus import Delivery
from app.engine.clock import Clock
from app.engine.gateway.gateway import ActionGateway
from app.engine.gateway.types import Source
from app.engine.metro.store import METRO_HISTORY, MetroRunStore
from app.engine.notify import NotifierPort
from app.engine.planner.decide import decide
from app.engine.planner.store import DecisionRecord, PlannerStore
from app.engine.planner.types import Act, Wait
from app.engine.scenarios.context import History, Reread, ScenarioContext
from app.engine.scenarios.library import ScenarioResult, run_scenario
from app.engine.scenarios.registry import CERTIFIED, SCENARIOS
from app.engine.settings import SettingsProvider
from app.engine.state.model import CharacterState

log = logging.getLogger(__name__)
# Серия неудач сценария: 5 мин, 10, 20… но не больше 2 ч.
RETRY_AFTER = timedelta(minutes=5)
MAX_RETRY = timedelta(hours=2)
# «Нечего делать» и «занят» уже обновили состояние; короткая пауза страхует от зацикливания.
NOTHING_RETRY = timedelta(minutes=1)
# «Нечего делать», которое за минуту не изменится: биржа не откроется до конца окна слива, цена
# входа в метро сама не вернётся.
NOTHING_HOLD: dict[tuple[str, str], timedelta] = {
    ("stocks_dump", "market_closed"): timedelta(minutes=30),
    ("metro", "entry_cost_changed"): timedelta(hours=2),
}
# Подавленное действие (dry_run) состояние не меняет: сценарий откладывается, решаются остальные.
SUPPRESSED_HOLD = timedelta(minutes=10)
# Подавление остановкой движка к сценарию не относится.
NOT_HELD = frozenset({"kill_switch", "shutdown"})
DEEDS = tuple(name for name in SCENARIOS if name.startswith("deed:"))
# Отказы, которые получит любое дело, а не только отказанное.
SHARED_REFUSALS = frozenset(
    {"battle_soon", "battle_running", "factory_running", "tired", "levelup_required"}
)


class FixedParams(ValueError):
    """Параметр ручного запуска противоречит параметру сценария из реестра."""


class InvalidParams(ValueError):
    """Обязательного параметра ручного запуска нет или он недопустим."""


@dataclass(frozen=True, slots=True)
class ManualRun:
    run_id: int
    scenario: str
    params: dict[str, Any]


def cooldown_key(act: Act) -> str:
    """Кулдаун и серия неудач рефреша — свои у каждого источника."""
    if act.scenario == "refresh":
        return f"refresh:{act.params['source']}"
    return act.scenario


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
        metro_store: MetroRunStore | None = None,
        history: History | None = None,
        reread: Reread | None = None,
        auto: bool = True,
    ) -> None:
        self._gateway = gateway
        # auto=False — только ручные запуски из админки, без собственных решений.
        self._auto = auto
        self._manual: deque[ManualRun] = deque()
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
        # Отложенные подавлением: снимаются при смене режима движка.
        self._held: dict[str, datetime] = {}
        self._mode: str | None = None
        self._failures: dict[str, int] = {}
        # Последний успешный запуск каждого сценария (кулдауны мандарина и т. п. после рестарта).
        self._last_done: dict[str, datetime] | None = None
        self._metro_store = metro_store
        self._history = history
        self._reread = reread
        # Длительности прошлых забегов метро (бюджет по p90): из хранилища при первом решении.
        self._metro_durations: list[float] | None = None
        self._last_wait: DecisionRecord | None = None
        self.current: str | None = None
        self.next_wake: datetime | None = None

    async def on_delivery(self, delivery: Delivery) -> None:
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    async def request(
        self, scenario: str, params: Mapping[str, Any], *, key: str, by: str
    ) -> tuple[int, bool]:
        """Ручной запуск сценария: в очередь перед решениями планировщика. Возвращает id
        запуска и признак, что он создан сейчас (иначе ключ уже встречался). KeyError — нет
        такого сценария, FixedParams — параметр противоречит зафиксированному в реестре,
        InvalidParams — обязательного параметра нет или он недопустим."""
        spec = SCENARIOS[scenario]
        fixed = spec.params
        clash = sorted(k for k, v in params.items() if k in fixed and fixed[k] != v)
        if clash:
            raise FixedParams(clash)
        merged = {**params, **fixed}
        if bad := spec.invalid(merged):
            raise InvalidParams(bad)
        run_id, created = await self._store.run_requested(
            scenario, merged, requested=params, key=key, by=by, at=self._clock.now()
        )
        if created:
            self._manual.append(ManualRun(run_id, scenario, merged))
            self._wake.set()
        return run_id, created

    async def run(self) -> None:
        while True:
            self._wake.clear()
            if self._manual:
                await self.run_manual()
                continue
            pause = await self.step() if self._auto else self._max_idle_s
            if pause is not None:
                await self._pause(pause)

    async def run_manual(self) -> None:
        """Следующий ручной запуск из очереди. Его сбой не роняет цикл планировщика."""
        item = self._manual.popleft()
        started = self._clock.now()
        # Режим ручного запуска фиксируется на его старте, как у запусков плана.
        dry_run = self._settings.current.engine.mode == "dry_run"
        try:
            await self._store.run_begin(item.run_id, started)
        except Exception:
            log.exception("manual run %d not started", item.run_id)
            await self._close_failed(item.run_id, "store_failed")
            return
        try:
            await self._perform(
                Act(item.scenario, item.params, "manual"),
                item.run_id,
                started,
                manual=True,
                dry_run=dry_run,
            )
        except Exception:
            log.exception("manual run %d of %s failed", item.run_id, item.scenario)

    async def _close_failed(self, run_id: int, reason: str) -> None:
        try:
            await self._store.run_finished(run_id, "failed", reason, self._clock.now())
        except Exception:
            # Строка остаётся queued: при рестарте close_running закроет её как cancelled.
            log.exception("manual run %d not closed", run_id)

    async def _pause(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), seconds)
        except TimeoutError:
            pass

    async def step(self) -> float | None:
        """Одно решение; возвращает, сколько ждать до следующего (None — сразу)."""
        now = self._clock.now()
        if self._ready() is not None:
            self.next_wake = None
            return self._poll_s
        settings = self._settings.current
        if self._last_done is None:
            self._last_done = await self._store.last_done()
        if self._metro_durations is None:
            self._metro_durations = await self._load_metro_durations()
        if settings.engine.mode != self._mode:
            self._held.clear()
            self._mode = settings.engine.mode
        decision = decide(
            self._state(),
            settings,
            now,
            certified=CERTIFIED if settings.engine.mode == "live" else None,
            last_refresh=self._last_refresh,
            cooldowns=self._blocked(),
            last_done=self._last_done,
            metro_durations=self._metro_durations,
        )
        if isinstance(decision, Wait):
            return await self._wait(now, decision)
        self._last_wait = None
        self.next_wake = None
        decision_id = await self._store.record(now, decision)
        # Режим запуска — тот, в котором принято решение.
        await self._execute(decision, decision_id, dry_run=settings.engine.mode == "dry_run")
        return None

    def _blocked(self) -> dict[str, datetime]:
        blocked = dict(self._cooldowns)
        for key, until in self._held.items():
            blocked[key] = max(until, blocked.get(key, until))
        return blocked

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

    async def _execute(self, act: Act, decision_id: int, *, dry_run: bool) -> None:
        spec = SCENARIOS[act.scenario]
        params = {**spec.params, **act.params}
        started = self._clock.now()
        run_id = await self._store.run_started(decision_id, act.scenario, params, started)
        await self._perform(
            replace(act, params=params), run_id, started, manual=False, dry_run=dry_run
        )

    def _paused(self, manual: bool) -> bool:
        engine = self._settings.current.engine
        return engine.paused and not (manual and engine.manual_while_paused)

    async def _perform(
        self, act: Act, run_id: int, started: datetime, *, manual: bool, dry_run: bool
    ) -> None:
        spec = SCENARIOS[act.scenario]
        ctx = ScenarioContext(
            self._gateway,
            game_chat_id=self._settings.current.chats.game_chat_id,
            simulate=not spec.certified,
            # Смена dry_run → live посреди сценария не делает его шаги реальными.
            dry_run=dry_run,
            paused=lambda: self._paused(manual),
            timeout_s=self._step_timeout_s,
            clock=self._clock,
            notifier=self._notifier,
            history=self._history,
            reread=self._reread,
            source=Source.MANUAL if manual else Source.SCENARIO,
        )
        self.current = act.scenario
        try:
            result = await run_scenario(spec.fn, ctx, self._state(), act.params)
        except Exception:
            log.exception("scenario %s crashed", act.scenario)
            result = ScenarioResult("failed", "crashed")
        finally:
            self.current = None
        if result.reason == "paused":
            result = replace(result, status="stopped")
        finished = self._clock.now()
        try:
            # Подавленный ручной запуск о планах ничего не говорит: откладывать сценарий незачем.
            if not (manual and result.status == "suppressed"):
                # Кулдаун — до записи в журнал: сбой БД не должен оставить сценарий без него.
                await self._after(act, result, started, finished)
        finally:
            # Сбой учёта итога (уведомление, БД) не оставляет запуск в running.
            await self._store.run_finished(run_id, result.status, result.reason, finished)
        if result.details is not None and "metro" in result.details:
            await self._save_metro(run_id, result)

    async def _load_metro_durations(self) -> list[float]:
        if self._metro_store is None:
            return []
        try:
            return await self._metro_store.durations()
        except Exception:
            log.exception("metro durations not loaded")
            return []

    async def _save_metro(self, run_id: int, result: ScenarioResult) -> None:
        assert result.details is not None
        record = result.details["metro"]
        if result.status == "done" and self._metro_durations is not None:
            self._metro_durations.append(float(record.get("duration_s", 0.0)))
            # Как и при загрузке — только последние забеги, иначе p90 зависит от аптайма.
            del self._metro_durations[:-METRO_HISTORY]
        if self._metro_store is None:
            return
        try:
            await self._metro_store.save(run_id, result.status, record)
        except Exception:
            log.exception("metro run not saved")

    async def _after(
        self, act: Act, result: ScenarioResult, started: datetime, finished: datetime
    ) -> None:
        name = act.scenario
        key = cooldown_key(act)
        if name == "refresh":
            self._last_refresh[str(act.params["source"])] = started
        if result.reason == "paused":
            return
        is_deed = name.startswith("deed:")
        if result.status == "suppressed":
            if result.reason in NOT_HELD:
                return
            # Подавленное дело означает «занят делом»: откладываются все дела.
            for held in DEEDS if is_deed else (key,):
                self._held[held] = finished + SUPPRESSED_HOLD
            return
        if result.status == "done":
            self._failures.pop(key, None)
            if self._last_done is not None:
                self._last_done[name] = started
            return
        if name == "tangerine" and result.status == "refused" and result.reason == "not_player":
            await self._notifier.notify(
                "warn", "tangerine_not_player", "tangerine recipient is not playing; paused 24h"
            )
        if result.status in ("failed", "stopped"):
            await self._failed(key, result, finished)
            return
        if result.status == "nothing" or result.reason == "busy":
            hold = NOTHING_HOLD.get((name, result.reason), NOTHING_RETRY)
            self._cooldowns[key] = finished + hold
            return
        shared = is_deed and result.reason in SHARED_REFUSALS
        for target in DEEDS if shared else (key,):
            self._cooldowns[target] = finished + RETRY_AFTER

    async def _failed(self, key: str, result: ScenarioResult, finished: datetime) -> None:
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        retry = RETRY_AFTER * 2 ** min(count - 1, 10)
        self._cooldowns[key] = finished + min(retry, MAX_RETRY)
        if count == 1:
            # Одно уведомление на серию неудач, до следующего успеха.
            await self._notifier.notify(
                "warn", "scenario_failed", f"{key}: {result.status} {result.reason}"
            )
