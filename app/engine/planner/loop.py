from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any

from app.engine.artifact import ArtifactRuns
from app.engine.bus import Delivery
from app.engine.clock import Clock
from app.engine.gametime import day_start, tasks_day, to_msk
from app.engine.gateway.gateway import RECONCILE_REASON, ActionGateway
from app.engine.gateway.types import Source
from app.engine.metro.store import METRO_HISTORY, MetroRunStore
from app.engine.notify import NotifierPort
from app.engine.planner.decide import Outlook, decide, lottery_params, outlook, resume_metro
from app.engine.planner.obligations import LOTTERY_OPEN
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
    # Тиража нет или продажа закрыта — до конца окна продажи не изменится.
    ("lottery_buy", "no_draw"): timedelta(minutes=30),
    ("lottery_buy", "lottery_closed"): timedelta(hours=2),
}
# /fb отдал отчёт не за сегодня — битва ещё не посчитана. По корпусу сегодняшний отчёт готов почти
# сразу (18:31, 19:02): старый после ~19:00 почти наверняка значит, что сегодняшнего не будет. Не
# больше трёх /fb за день с растущей паузой (18:31 → 18:46 → 19:16), дальше — до следующего дня.
FACTORY_REPORT_TRIES = 3
FACTORY_REPORT_BACKOFF = (timedelta(minutes=15), timedelta(minutes=30))
# Тираж мог открыться на секунды позже 19:17: «тиража нет» в начале окна продажи, до 19:30,
# повторяется через 2 минуты.
LOTTERY_LATE_OPEN = time(19, 30)
LOTTERY_LATE_OPEN_HOLD = timedelta(minutes=2)
# Подавленное действие (dry_run) состояние не меняет: сценарий откладывается, решаются остальные.
SUPPRESSED_HOLD = timedelta(minutes=10)
# Подавление остановкой движка к сценарию не относится.
NOT_HELD = frozenset({"kill_switch", "shutdown"})
DEEDS = tuple(name for name in SCENARIOS if name.startswith("deed:"))
# Отказы, которые получит любое дело, а не только отказанное.
SHARED_REFUSALS = frozenset(
    {"battle_soon", "battle_running", "factory_running", "tired", "levelup_required"}
)
# Отказ из-за уровня или профессии за минуты не изменится: пауза сценария до следующих суток. Пауза
# в памяти, перезапуск её сбрасывает — одна лишняя попытка допустима.
LONG_REFUSALS = frozenset({"min_level", "not_harvester"})


class FixedParams(ValueError):
    """Параметр ручного запуска противоречит параметру сценария из реестра."""


class InvalidParams(ValueError):
    """Обязательного параметра ручного запуска нет или он недопустим."""


@dataclass(frozen=True, slots=True)
class ManualRun:
    run_id: int
    scenario: str
    params: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LoopView:
    """Что сейчас с исполнением: решение планировщика при паузе или неготовности — условное."""

    paused: bool
    # Причина неготовности (`paused`, `killed`, `tg_offline`, …) или None.
    ready: str | None
    # Планировщик принимает свои решения (иначе — только ручные запуски).
    auto: bool
    current: str | None
    # Параметры идущего запуска (с зафиксированными в реестре); None — ничего не идёт.
    current_params: dict[str, Any] | None
    manual_queue: int
    next_wake: datetime | None
    # Ожидание, в котором цикл спит: его причина (как в журнале) и когда цикл проснётся сам —
    # `next_wake`, но не позже `max_idle_s` от решения; сообщение игры будит раньше. None — цикл
    # не ждёт.
    wait_reason: str | None
    wake_at: datetime | None


@dataclass(frozen=True, slots=True)
class PlanView:
    now: datetime
    outlook: Outlook
    loop: LoopView


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
        artifacts: ArtifactRuns | None = None,
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
        self._artifacts = artifacts
        # Длительности прошлых забегов метро (бюджет по p90): из хранилища при первом решении.
        self._metro_durations: list[float] | None = None
        # Запуски отчёта о фабрике (/fb) за день: при смене дня — из хранилища (переживает
        # рестарт), дальше — по итогам своих запусков.
        self._factory_tries: tuple[date, int] | None = None
        # Успешные запуски дел за день заданий (чередование основных дел): при смене дня — заново
        # из хранилища, дальше — по итогам своих запусков.
        self._done_today: tuple[date, dict[str, int]] | None = None
        self._last_wait: DecisionRecord | None = None
        # Нехватка, которую последний запуск лотереи увидел сверх запасов: (тираж, валюты).
        self._lottery_short: tuple[int, dict[str, int]] | None = None
        self.current: str | None = None
        self.current_params: dict[str, Any] | None = None
        self.next_wake: datetime | None = None
        self._waiting: tuple[str, datetime] | None = None
        # Отметка цикла: растёт при каждом изменении его входов (решение, запуск, очередь).
        self.revision = 0
        # Задача цикла идёт: до старта и в паузе супервизора после падения — нет.
        self.running = False

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
            self.revision += 1
            self._wake.set()
        return run_id, created

    async def run(self) -> None:
        self.running = True
        try:
            while True:
                self._wake.clear()
                if self._manual:
                    await self.run_manual()
                    continue
                pause = await self.step() if self._auto else await self._idle()
                if pause is not None:
                    await self._pause(pause)
        finally:
            self.running = False

    async def _idle(self) -> float:
        # Без собственных решений цикл всё равно закрывает сбор артефакта по сроку.
        await self._artifact_tick()
        return self._max_idle_s

    async def run_manual(self) -> None:
        """Следующий ручной запуск из очереди. Его сбой не роняет цикл планировщика."""
        item = self._manual.popleft()
        self.revision += 1
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
                Act(item.scenario, self._manual_params(item, started), "manual"),
                item.run_id,
                started,
                manual=True,
                dry_run=dry_run,
            )
        except Exception:
            log.exception("manual run %d of %s failed", item.run_id, item.scenario)

    def _manual_params(self, item: ManualRun, now: datetime) -> dict[str, Any]:
        """Недостающие параметры ручной лотереи — как у запуска планировщика: без них «все
        билеты без запаса» потратили бы запасы и резервы."""
        if item.scenario != "lottery_buy":
            return item.params
        settings = self._settings.current
        certified = CERTIFIED if settings.engine.mode == "live" else None
        planned = lottery_params(self._state(), settings, now, certified=certified)
        return {**planned, **item.params}

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
        try:
            return await self._step()
        finally:
            self.revision += 1

    async def _step(self) -> float | None:
        now = self._clock.now()
        await self._artifact_tick()
        if (ready := self._ready()) is not None:
            self.next_wake = None
            self._waiting = None
            if ready == "spending_blocked" and await self._resume_metro(now):
                return None
            return self._poll_s
        settings = self._settings.current
        if self._last_done is None:
            self._last_done = await self._store.last_done()
        if self._metro_durations is None:
            self._metro_durations = await self._load_metro_durations()
        done_today = await self._deeds_today(now)
        await self._factory_report_tries(now)
        if settings.engine.mode != self._mode:
            self._held.clear()
            self._mode = settings.engine.mode
        decision = decide(
            self._observed(),
            settings,
            now,
            certified=CERTIFIED if settings.engine.mode == "live" else None,
            last_refresh=self._last_refresh,
            cooldowns=self._blocked(),
            last_done=self._last_done,
            metro_durations=self._metro_durations,
            done_today=done_today,
        )
        if isinstance(decision, Wait):
            return await self._wait(now, decision)
        self._last_wait = None
        self.next_wake = None
        self._waiting = None
        decision_id = await self._store.record(now, decision)
        # Режим запуска — тот, в котором принято решение.
        await self._execute(decision, decision_id, dry_run=settings.engine.mode == "dry_run")
        return None

    async def _resume_metro(self, now: datetime) -> bool:
        """Под блоком трат до сверки — только продолжение забега метро: сверка ждёт его конца."""
        if self._gateway.spending_blocked != RECONCILE_REASON:
            return False
        settings = self._settings.current
        if self._last_done is None:
            self._last_done = await self._store.last_done()
        if self._metro_durations is None:
            self._metro_durations = await self._load_metro_durations()
        act = resume_metro(
            self._observed(),
            settings,
            now,
            certified=CERTIFIED if settings.engine.mode == "live" else None,
            cooldowns=self._blocked(),
            last_done=self._last_done,
            metro_durations=self._metro_durations,
        )
        if act is None:
            return False
        decision_id = await self._store.record(now, act)
        await self._execute(act, decision_id, dry_run=settings.engine.mode == "dry_run")
        return True

    async def _artifact_tick(self) -> None:
        """Окончание сбора артефакта и сверка его запуска: в игру ничего не шлёт — и до
        готовности (после старта движка пропущенное окончание закрывается первым шагом)."""
        if self._artifacts is None:
            return
        try:
            await self._artifacts.tick()
        except Exception:
            log.exception("artifact run tick failed")

    async def outlook(self) -> PlanView:
        """«План бота»: проход планировщика и состояние цикла."""
        now, view = await self.plan()
        return PlanView(now, view, self.loop_view())

    async def plan(self) -> tuple[datetime, Outlook]:
        """Проход планировщика на тех же входах, что у `step()`, без решения и записи в журнал.
        Кеши цикла только читаются: пустой кеш читается из хранилища в локальную переменную —
        иначе поздний ответ этого чтения затёр бы кеш, который `step()` уже загрузил и обновил."""
        now = self._clock.now()
        settings = self._settings.current
        last_done = self._last_done
        if last_done is None:
            try:
                last_done = await self._store.last_done()
            except Exception:
                # Без кулдаунов последних запусков решаем как после рестарта; перечитаем на
                # следующем проходе — кеш цикла (`step()`) сбой здесь не трогает.
                log.exception("last done not loaded for outlook")
                last_done = {}
        durations = self._metro_durations
        if durations is None:
            durations = await self._load_metro_durations()
        done_today = await self._peek_today(now)
        # Смена режима снимет отсрочки подавления на следующем решении — план их уже не видит.
        held = settings.engine.mode == self._mode
        view = outlook(
            self._observed(),
            settings,
            now,
            certified=CERTIFIED if settings.engine.mode == "live" else None,
            last_refresh=self._last_refresh,
            cooldowns=self._blocked() if held else dict(self._cooldowns),
            last_done=last_done,
            metro_durations=durations,
            done_today=done_today,
        )
        return now, view

    def loop_view(self) -> LoopView:
        return LoopView(
            paused=self._settings.current.engine.paused,
            ready=self._ready(),
            auto=self._auto,
            current=self.current,
            current_params=self.current_params,
            manual_queue=len(self._manual),
            next_wake=self.next_wake,
            wait_reason=self._waiting[0] if self._waiting is not None else None,
            wake_at=self._waiting[1] if self._waiting is not None else None,
        )

    async def _peek_today(self, now: datetime) -> dict[str, int]:
        """Счётчик дел за день для плана: из кеша цикла, если он за этот день, иначе из
        хранилища без записи в кеш."""
        day = tasks_day(now)
        if self._done_today is not None and self._done_today[0] == day:
            return self._done_today[1]
        try:
            return await self._store.done_on_day(day)
        except Exception:
            log.exception("deeds done on %s not loaded for outlook", day)
            return {}

    def _observed(self) -> CharacterState:
        """Состояние с нехваткой, которую последний запуск лотереи увидел сверх запасов и
        резервов: снимок тиража её не знает, а без неё устаревший ресурс снова открывал бы экран
        лотереи вместо профиля."""
        state = self._state()
        seen, noted = state.lottery, self._lottery_short
        if seen is None or noted is None or seen.value.draw != noted[0]:
            return state
        snap = seen.value.model_copy(update={"short": {**seen.value.short, **noted[1]}})
        return state.model_copy(update={"lottery": seen.model_copy(update={"value": snap})})

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
        wake = now + timedelta(seconds=self._max_idle_s)
        if decision.until is not None:
            wake = max(min(decision.until, wake), now)
        self._waiting = (decision.reason, wake)
        return (wake - now).total_seconds()

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
            run_id=run_id,
            scenario=act.scenario,
        )
        self.current = act.scenario
        self.current_params = dict(act.params)
        self.revision += 1
        try:
            result = await run_scenario(spec.fn, ctx, self._state(), act.params)
        except Exception:
            log.exception("scenario %s crashed", act.scenario)
            result = ScenarioResult("failed", "crashed")
        finally:
            self.current = None
            self.current_params = None
            self.revision += 1
        if result.reason == "paused":
            result = replace(result, status="stopped")
        if act.scenario == "lottery_buy":
            self._lottery_short = _lottery_short(result)
        finished = self._clock.now()
        try:
            # Подавленный ручной запуск о планах ничего не говорит: откладывать сценарий незачем.
            if not (manual and result.status == "suppressed"):
                # Кулдаун — до записи в журнал: сбой БД не должен оставить сценарий без него.
                await self._after(act, result, started, finished)
        finally:
            self.revision += 1
            # Сбой учёта итога (уведомление, БД) не оставляет запуск в running.
            await self._store.run_finished(run_id, result.status, result.reason, finished)
        if result.details is not None and "metro" in result.details:
            await self._save_metro(run_id, result)

    async def _deeds_today(self, now: datetime) -> dict[str, int]:
        day = tasks_day(now)
        if self._done_today is None or self._done_today[0] != day:
            try:
                counts = await self._store.done_on_day(day)
            except Exception:
                # Без счётчика решаем как в начале дня; перечитаем на следующем решении.
                log.exception("deeds done on %s not loaded", day)
                return {}
            self._done_today = (day, counts)
        return self._done_today[1]

    async def _factory_report_tries(self, now: datetime) -> None:
        """Запуски /fb за сегодня — из хранилища при смене дня: исчерпанные в прошлом процессе
        попытки держат сценарий до завтра."""
        day = tasks_day(now)
        if self._factory_tries is not None and self._factory_tries[0] == day:
            return
        try:
            tries = await self._store.runs_on_day("factory_report", day)
        except Exception:
            log.exception("factory report runs on %s not loaded", day)
            # Без счётчика /fb не шлём вслепую: перечитаем через минуту.
            self._hold("factory_report", now + NOTHING_RETRY)
            return
        self._factory_tries = (day, tries)
        if tries >= FACTORY_REPORT_TRIES:
            self._hold("factory_report", day_start(day + timedelta(days=1)))

    def _hold(self, key: str, until: datetime) -> None:
        current = self._cooldowns.get(key)
        self._cooldowns[key] = until if current is None else max(current, until)

    def _factory_report_try(self, started: datetime) -> int:
        """Ещё одна попытка /fb в день её начала; номер попытки за день."""
        day = tasks_day(started)
        known = self._factory_tries
        tries = (known[1] if known is not None and known[0] == day else 0) + 1
        self._factory_tries = (day, tries)
        return tries

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
        elif name == "daily_refresh":
            self._last_refresh["daily"] = started
        elif name == "trips_refresh":
            self._last_refresh["trips"] = started
        if result.reason == "paused":
            return
        if name == "artifact_start" and self._artifacts is not None:
            try:
                details = {"artifact": act.params.get("artifact"), **(result.details or {})}
                await self._artifacts.started(result.status, result.reason, details)
            except Exception:
                log.exception("artifact start result not applied")
        is_deed = name.startswith("deed:")
        if result.status == "suppressed":
            if result.reason in NOT_HELD:
                return
            # Подавленное дело означает «занят делом»: откладываются все дела.
            for held in DEEDS if is_deed else (key,):
                self._held[held] = finished + SUPPRESSED_HOLD
            return
        tries = self._factory_report_try(started) if name == "factory_report" else 0
        if result.status == "done":
            self._failures.pop(key, None)
            if self._last_done is not None:
                self._last_done[name] = started
            today = self._done_today
            # Запуск относится к дню своего начала: вчерашний сегодняшний счётчик не меняет.
            if is_deed and today is not None and tasks_day(started) == today[0]:
                today[1][name] = today[1].get(name, 0) + 1
            return
        if name == "tangerine" and result.status == "refused" and result.reason == "not_player":
            await self._notifier.notify(
                "warn", "tangerine_not_player", "tangerine recipient is not playing; paused 24h"
            )
        if result.status in ("failed", "stopped"):
            await self._failed(key, result, finished)
        elif (name, result.reason) == ("factory_report", "old_report"):
            backoff = FACTORY_REPORT_BACKOFF[min(tries, len(FACTORY_REPORT_BACKOFF)) - 1]
            self._cooldowns[key] = finished + backoff
        elif result.status == "nothing" or result.reason == "busy":
            hold = NOTHING_HOLD.get((name, result.reason), NOTHING_RETRY)
            if (name, result.reason) == ("lottery_buy", "no_draw"):
                if LOTTERY_OPEN <= to_msk(finished).time() < LOTTERY_LATE_OPEN:
                    hold = LOTTERY_LATE_OPEN_HOLD
            self._cooldowns[key] = finished + hold
        elif result.status == "refused" and result.reason in LONG_REFUSALS:
            self._cooldowns[key] = day_start(tasks_day(started) + timedelta(days=1))
        else:
            shared = is_deed and result.reason in SHARED_REFUSALS
            for target in DEEDS if shared else (key,):
                self._cooldowns[target] = finished + RETRY_AFTER
        if tries >= FACTORY_REPORT_TRIES:
            # Третий /fb за день без сегодняшнего отчёта — до завтра, каким бы ни был исход.
            self._cooldowns[key] = day_start(tasks_day(started) + timedelta(days=1))

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


def _lottery_short(result: ScenarioResult) -> tuple[int, dict[str, int]] | None:
    note = (result.details or {}).get("lottery")
    if note is None:
        return None
    return int(note["draw"]), {str(c): int(v) for c, v in note["short"].items()}
