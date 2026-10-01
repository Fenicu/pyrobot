"""Хост движков (раздел 4.2 спеки): движки всех включённых аккаунтов в одном процессе.
Желаемое состояние — в `accounts` (статус и поколение движка): хост сверяет с ним свои движки по
`poke()` и раз в `reconcile_s`. Аккаунтом на хосте в каждый момент занят кто-то один (замок
аккаунта): новый движок стартует только после полной остановки прежнего. Старты идут по одному с
паузой `start_gap_s` — без залпа подключений к Telegram с одного IP. Падение одного аккаунта
(серия сбоев задачи, ошибка старта, потеря аренды) остальные движки не трогает."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.db.accounts import AccountStatus
from app.db.notifications import DbNotifier
from app.engine.fence import Fence, LeaseLost
from app.engine.host.account import AccountRuntime, RuntimeDeps
from app.engine.host.lease import Busy, LeaseManager
from app.engine.notify import Level, LogNotifier
from app.engine.supervisor import Supervisor

log = logging.getLogger(__name__)

_STATUSES: tuple[AccountStatus, ...] = ("enabled", "disabled", "error", "deleting")


@dataclass(frozen=True)
class HostStatus:
    """Здоровье хоста — отдельно от статусов аккаунтов."""

    holder: str
    # Соединение блокировок открыто: без него аренды не захватываются и не продлеваются.
    lock_connection_ok: bool
    # Аккаунты с зарегистрированным движком.
    engines: list[int]
    # Аккаунты, которые не удалось захватить: `locked_elsewhere` или `lease_active`.
    busy: dict[int, str]
    loop_lag_ms: float
    # Задачи процесса (сверка, продление аренды и прочие) идут, ни одна не ждёт перезапуска.
    tasks_ok: bool


@dataclass(eq=False)
class _Engine:
    """Движок аккаунта на хосте — от захвата аренды до полной остановки."""

    runtime: AccountRuntime
    # Зарегистрирован: его отдаёт `get()`.
    live: bool = False
    # Остановка назначена или идёт: движок уже не регистрируется, остановка не назначается снова.
    ending: bool = False
    # Аренда потеряна: ограда отозвана или наступил её срок.
    lost: bool = False
    # Задача, ушедшая в серию сбоев.
    crash: str | None = None


class EngineHost:
    """Движки аккаунтов процесса и реестр движков для API (`EngineRegistry`)."""

    def __init__(
        self,
        deps: RuntimeDeps,
        leases: LeaseManager,
        *,
        max_engines: int,
        start_gap_s: float,
        reconcile_s: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.capacity = max_engines
        self._deps = deps
        self._leases = leases
        self._start_gap = start_gap_s
        self._reconcile_s = reconcile_s
        self._sleep = sleep
        # Супервизор процесса: сверка хоста и задачи процесса (`app.main`). Без предела сбоев:
        # задача процесса — например, продление аренды — не бросается после серии падений, а
        # перезапускается с паузой до минуты. Уведомлять некого (у процесса нет аккаунта) —
        # сбои в логе.
        self.supervisor = Supervisor(LogNotifier(), crash_limit=None)
        # Движок каждого аккаунта — от захвата аренды до полной остановки.
        self._engines: dict[int, _Engine] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        # Захваты аренды — по одному: соединение блокировок одно, продление встаёт в очередь за
        # захватами, и несколько захватов, ждущих строки аккаунтов, задержали бы его.
        self._acquiring = asyncio.Lock()
        # Почему аккаунт не захвачен (`Busy.reason`) и когда повторить захват (часы цикла).
        self._reasons: dict[int, str] = {}
        self._retry_at: dict[int, float] = {}
        self._wake = asyncio.Event()
        # Взводится и заменяется при каждой регистрации и снятии движка (`wait_registered`).
        self._changed = asyncio.Event()
        # Остановки движков, назначенные из ограды и супервизоров движков.
        self._ops: set[asyncio.Task[None]] = set()
        self._last_start: float | None = None

    async def start(self) -> None:
        """Аккаунты без владельца достаются первой учётке, и запускается цикл сверки. Движки
        всех `enabled` поднимает его первый проход — в фоне, по возрастанию `id` с паузой между
        стартами: HTTP плавного старта не ждёт."""
        await self._deps.accounts.adopt_orphans()
        self.poke()
        self.supervisor.start("reconcile", self._run)

    async def stop(self) -> None:
        """Сверка — первой, вместе с незаконченным плавным стартом: новый старт не начнётся
        посреди остановки, начатый остановится и освободит аренду. Затем все движки разом:
        штатно, пока аренда действует (иначе аварийно), и освобождение аренды каждого. Продление
        аренд и соединение блокировок останавливает процесс — после этого."""
        await self.supervisor.cancel("reconcile")
        await asyncio.gather(*(self._shutdown(account_id) for account_id in list(self._engines)))
        # Назначенные остановки уже остановленных движков ничего не делают.
        await asyncio.gather(*self._ops, return_exceptions=True)

    def get(self, account_id: int) -> AccountRuntime | None:
        engine = self._engines.get(account_id)
        return engine.runtime if engine is not None and engine.live else None

    async def wait_registered(self, account_id: int, timeout_s: float) -> AccountRuntime | None:
        try:
            async with asyncio.timeout(timeout_s):
                while True:
                    runtime = self.get(account_id)
                    if runtime is not None:
                        return runtime
                    await self._changed.wait()
        except TimeoutError:
            return None

    def host_reason(self, account_id: int) -> str | None:
        return None if self.get(account_id) is not None else self._reasons.get(account_id)

    def poke(self) -> None:
        """Сверить желаемое состояние сейчас, не дожидаясь `reconcile_s`."""
        self._wake.set()

    def status(self) -> HostStatus:
        return HostStatus(
            holder=self._leases.holder,
            lock_connection_ok=self._leases.healthy(),
            engines=sorted(a for a, engine in self._engines.items() if engine.live),
            busy=dict(self._reasons),
            loop_lag_ms=self._deps.lag.lag_ms,
            tasks_ok=self.supervisor.healthy(),
        )

    async def _run(self) -> None:
        """Цикл сверки: по `poke()`, раз в `reconcile_s` и к сроку повтора захвата."""
        loop = asyncio.get_running_loop()
        while True:
            now = loop.time()
            due = [at - now for at in self._retry_at.values() if at > now]
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(min([self._reconcile_s, *due])):
                    await self._wake.wait()
            self._wake.clear()
            try:
                await self._reconcile()
            except Exception:
                log.exception("engines not reconciled")

    async def _reconcile(self) -> None:
        """Движок аккаунта, который не `enabled` (или удалён), и движок не того поколения
        останавливаются штатно; `enabled` без движка захватываются и стартуют по возрастанию `id`.
        Аккаунт, занятый остановкой, ждёт её конца: она сама будит сверку."""
        accounts = {a.id: a for a in await self._deps.accounts.with_status(*_STATUSES)}
        for account_id, engine in list(self._engines.items()):
            account = accounts.get(account_id)
            if (
                account is None
                or account.status != "enabled"
                or account.engine_generation != engine.runtime.generation
            ):
                self._end(account_id, engine)
        wanted = sorted(a.id for a in accounts.values() if a.status == "enabled")
        for account_id in (set(self._reasons) | set(self._retry_at)) - set(wanted):
            self._reasons.pop(account_id, None)
            self._retry_at.pop(account_id, None)
        loop = asyncio.get_running_loop()
        for account_id in wanted:
            if account_id in self._engines or self._lock(account_id).locked():
                continue
            if self._retry_at.get(account_id, 0.0) > loop.time():
                continue
            try:
                await self._start_engine(account_id)
            except Exception:
                log.exception("engine of account %d not started", account_id)

    async def _start_engine(self, account_id: int) -> None:
        """Захват аренды и старт движка под замком аккаунта. Занят — причина и срок повтора;
        аренда потеряна при старте — захват на следующей сверке; ошибка старта — `error` с
        причиной. Сбой старта уже остановил то, что успело подняться: остаётся освободить
        аренду."""
        loop = asyncio.get_running_loop()
        async with self._lock(account_id):
            if account_id in self._engines:
                return
            got = await self._acquire(account_id)
            if isinstance(got, Busy):
                self._reasons[account_id] = got.reason
                self._retry_at[account_id] = loop.time() + got.retry_in_s
                return
            self._reasons.pop(account_id, None)
            self._retry_at.pop(account_id, None)
            fence = got
            # Вызывается синхронно из ограды: только назначает остановку.
            fence.on_lost = lambda: self._lost(account_id, fence)
            engine: _Engine | None = None
            try:
                await self._gap()
                # Статус и поколение — после захвата: правка до него уже видна.
                account = await self._deps.accounts.get(account_id)
                if account is not None and account.status == "enabled" and fence.alive:
                    runtime = AccountRuntime(
                        account, self._deps, fence, on_crash_loop=self._crash_loop
                    )
                    engine = self._engines[account_id] = _Engine(runtime)
                    try:
                        await runtime.start()
                    finally:
                        self._last_start = loop.time()
            except Exception as exc:
                if engine is None:
                    raise
                del self._engines[account_id]
                if isinstance(exc, LeaseLost) or not fence.alive:
                    log.warning("account %d lease lost while engine started", account_id)
                    text = f"account {account_id} lease lost; engine stopped"
                    await self._notify(account_id, "error", "lock_lost", text)
                    self.poke()
                    return
                log.exception("engine of account %d failed to start", account_id)
                # Статус — до освобождения аренды: иначе сверка (этого или другого хоста) подняла
                # бы аккаунт снова.
                await self._set_error(account_id, f"start_failed:{type(exc).__name__}")
                return
            except BaseException:
                self._engines.pop(account_id, None)
                raise
            finally:
                if account_id not in self._engines:
                    await self._complete(self._leases.release(fence))
            if engine is None:
                # Аренда потеряна до старта — захват на следующей сверке.
                if not fence.alive:
                    self.poke()
                return
            # Серия сбоев или потеря аренды во время старта: движок не регистрируется, его
            # остановка уже назначена.
            if not engine.ending:
                engine.live = True
                self._registry_changed()

    async def _acquire(self, account_id: int) -> Fence | Busy:
        """Захваты — по одному. Запрос захвата доходит до конца и при отмене сверки (взятое
        освобождается): отмена запроса по соединению блокировок рвёт соединение вместе со
        всеми блокировками хоста."""
        async with self._acquiring:
            task = asyncio.ensure_future(self._leases.acquire(account_id))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.wait([task])
                if not task.cancelled() and task.exception() is None:
                    got = task.result()
                    if isinstance(got, Fence):
                        await self._leases.release(got)
                raise

    async def _complete(self, aw: Awaitable[None]) -> None:
        """Запрос по соединению блокировок доходит до конца и при отмене вызывающего; отмена
        выходит наружу после него."""
        task = asyncio.ensure_future(aw)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.wait([task])
            raise

    async def _gap(self) -> None:
        """Пауза перед стартом движка, если предыдущий стартовал меньше `start_gap_s` назад."""
        last = self._last_start
        if last is not None and asyncio.get_running_loop().time() - last < self._start_gap:
            await self._sleep(self._start_gap)

    def _lost(self, account_id: int, fence: Fence) -> None:
        engine = self._engines.get(account_id)
        if engine is None or engine.runtime.fence is not fence:
            return
        engine.lost = True
        self._end(account_id, engine)

    async def _crash_loop(self, account_id: int, task: str) -> None:
        """Серия сбоев задачи движка. Вызывается из задачи самого движка, поэтому остановка —
        отдельной задачей."""
        engine = self._engines.get(account_id)
        if engine is None:
            return
        log.error("account %d task %s crashed repeatedly, engine stopped", account_id, task)
        if engine.crash is None:
            engine.crash = task
        self._end(account_id, engine)

    def _end(self, account_id: int, engine: _Engine) -> None:
        """Движок снимается с регистрации сразу, остановка назначается один раз."""
        self._hide(engine)
        if engine.ending:
            return
        engine.ending = True
        task = asyncio.create_task(self._halt(account_id, engine), name=f"halt-{account_id}")
        self._ops.add(task)
        task.add_done_callback(self._ops.discard)

    async def _halt(self, account_id: int, engine: _Engine) -> None:
        try:
            async with self._lock(account_id):
                if self._engines.get(account_id) is not engine:
                    return
                await self._finish(account_id, engine)
        except Exception:
            log.exception("engine of account %d not stopped", account_id)
        self.poke()

    async def _shutdown(self, account_id: int) -> None:
        async with self._lock(account_id):
            engine = self._engines.get(account_id)
            if engine is not None:
                await self._finish(account_id, engine)

    async def _finish(self, account_id: int, engine: _Engine) -> None:
        """Остановка движка под замком аккаунта: штатная, пока аренда действует (доработка
        конвейера идёт под продлением), иначе аварийная; затем освобождение аренды. Серия
        сбоев — `error` и уведомление до освобождения."""
        engine.ending = True
        self._hide(engine)
        runtime = engine.runtime
        fence = runtime.fence
        try:
            await (runtime.stop() if fence.alive else runtime.abort())
        except Exception:
            log.exception("engine of account %d failed to stop", account_id)
        if engine.crash is not None:
            reason = f"crash_loop:{engine.crash}"
            await self._set_error(account_id, reason)
            await self._notify(
                account_id,
                "error",
                "account_crash_loop",
                f"account {account_id} engine stopped: {reason}",
            )
        elif engine.lost:
            log.warning("account %d lease lost, engine aborted", account_id)
            text = f"account {account_id} lease lost; engine stopped"
            await self._notify(account_id, "error", "lock_lost", text)
        await self._leases.release(fence)
        del self._engines[account_id]
        self._reasons.pop(account_id, None)

    async def _set_error(self, account_id: int, reason: str) -> None:
        try:
            await self._deps.accounts.set_status(account_id, "error", reason)
        except Exception:
            log.exception("account %d status %s not saved", account_id, reason)

    async def _notify(self, account_id: int, level: Level, code: str, text: str) -> None:
        # Без ограды: уведомление хоста пишется и после потери аренды.
        await DbNotifier(self._deps.db, account_id).notify(level, code, text)

    def _hide(self, engine: _Engine) -> None:
        if engine.live:
            engine.live = False
            self._registry_changed()

    def _registry_changed(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    def _lock(self, account_id: int) -> asyncio.Lock:
        return self._locks.setdefault(account_id, asyncio.Lock())
