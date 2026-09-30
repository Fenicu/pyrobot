import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI

from app.api.app import create_api
from app.api.container import Container
from app.api.security import LoginRateLimiter
from app.config import AppConfig
from app.db.accounts import AccountRepo
from app.db.auth_repo import AuthRepo
from app.db.base import Database
from app.db.notifications import DbNotifier
from app.db.reads import DbReads
from app.db.retention import DbRetention
from app.db.settings_store import DbSettingsStore
from app.engine.clock import SystemClock
from app.engine.host.account import AccountRuntime, RuntimeDeps
from app.engine.host.lease import Busy, LeaseManager
from app.engine.lag import LoopLagMonitor
from app.engine.supervisor import Supervisor

log = logging.getLogger("pyrobot")

SESSION_PURGE_S = 3600.0
# Ретеншн: первый проход не в момент старта (там догон пропусков), дальше — раз в 6 часов.
RETENTION_FIRST_S = 300.0
RETENTION_S = 6 * 3600.0
# Повтор захвата и старта движка после ошибки (база, старт движка).
ENGINE_RETRY_S = 30.0


class Runtime:
    """Процесс: база, вход в админку, `Container` и HTTP, аренды и задачи процесса (продление
    аренды, монитор задержки цикла, очистка сессий админки, ретеншн). Движок аккаунта
    `config.account_id` — `AccountRuntime` — стартует после захвата его аренды."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.db = Database(config.database_url)
        self.accounts = AccountRepo(self.db)
        self.auth = AuthRepo(self.db)
        # Без ограды: уведомления процесса об аккаунте (занят другим экземпляром, аренда
        # потеряна, серия сбоев) пишутся и без его аренды.
        self.notifier = DbNotifier(self.db, config.account_id)
        self.lag = LoopLagMonitor()
        self.leases = LeaseManager(self.db, uuid4().hex)
        self.container = Container(
            config=config,
            auth=self.auth,
            limiter=LoginRateLimiter(),
            reads=DbReads(self.db, config.account_id),
        )
        self.supervisor = Supervisor(self.notifier)
        self.session_purge_s = SESSION_PURGE_S
        self.retention = DbRetention(self.db, config.account_id)
        self.retention_first_s = RETENTION_FIRST_S
        self.retention_s = RETENTION_S
        self.engine_retry_s = ENGINE_RETRY_S
        self.account: AccountRuntime | None = None
        # Будит цикл движка: аренда потеряна (fence.on_lost) или серия сбоев задачи.
        self._down = asyncio.Event()
        # Серия сбоев задачи: движок не поднимается до перезапуска процесса.
        self._halted: str | None = None
        self._retry_in = 0.0
        # Последнее уведомление о том, что движка нет: повтор того же не шлётся.
        self._problem: str | None = None

    async def start(self) -> None:
        try:
            await self._start()
        except BaseException:
            await self.stop()
            raise

    async def _start(self) -> None:
        password = self.config.admin_password
        await self.auth.ensure_admin(
            self.config.admin_login, password.get_secret_value() if password else None
        )
        await self.leases.open()
        self.supervisor.start("lease", self.leases.run)
        self.supervisor.start("lag", self.lag.run)
        self.supervisor.start("session-purge", self._purge_sessions)
        self.supervisor.start("retention", self._retention)
        # Первая попытка — до готовности HTTP: как и раньше, процесс начинает с движком.
        await self._attempt()
        self.supervisor.start("engine", self._keep_engine)

    async def _keep_engine(self) -> None:
        """Без движка — захват аренды с повтором (`lease_active` — по её сроку,
        `locked_elsewhere` — через `busy_retry_s` менеджера, ошибка — через `engine_retry_s`);
        потеря аренды — аварийная остановка и новый захват; серия сбоев задачи — аварийная
        остановка, и движок не поднимается до перезапуска процесса."""
        while True:
            runtime = self.account
            if runtime is not None:
                await self._down.wait()
                await self._abort_engine(runtime)
            elif self._halted is not None:
                await asyncio.Event().wait()
            else:
                await asyncio.sleep(self._retry_in)
                await self._attempt()

    async def _attempt(self) -> None:
        account_id = self.config.account_id
        try:
            got = await self._try_engine()
        except Exception:
            log.exception("engine of account %d not started", account_id)
            self._retry_in = self.engine_retry_s
            await self._report(
                "engine_start_failed", f"engine of account {account_id} not started"
            )
            return
        if isinstance(got, Busy):
            self._retry_in = got.retry_in_s
            await self._report(
                "second_instance",
                f"account {account_id} is held by another pyrobot instance ({got.reason})",
            )
            return
        self._problem = None

    async def _try_engine(self) -> Busy | None:
        """Захват аренды аккаунта и старт его движка; `Busy` — аккаунт занят."""
        account_id = self.config.account_id
        got = await self.leases.acquire(account_id)
        if isinstance(got, Busy):
            return got
        down = asyncio.Event()
        # Вызывается синхронно из ограды: только будит цикл движка.
        got.on_lost = down.set
        try:
            account = await self.accounts.get(account_id)
            if account is None:
                raise RuntimeError(f"account {account_id} not found")
            deps = RuntimeDeps(
                db=self.db, config=self.config, accounts=self.accounts, lag=self.lag
            )
            runtime = AccountRuntime(account, deps, got, on_crash_loop=self._crash_loop)
            await runtime.start()
        except BaseException:
            await self.leases.release(got)
            raise
        self._down = down
        self.account = runtime
        self.container.facade = runtime.facade
        return None

    async def _abort_engine(self, runtime: AccountRuntime) -> None:
        self.container.facade = None
        await runtime.abort()
        await self.leases.release(runtime.fence)
        self.account = None
        self._retry_in = 0.0
        if self._halted is not None:
            await self.notifier.notify(
                "error",
                "account_crash_loop",
                f"account {runtime.account_id} engine stopped: {self._halted}",
            )
            return
        log.warning("account %d lease lost, engine aborted", runtime.account_id)
        await self.notifier.notify(
            "error", "lock_lost", f"account {runtime.account_id} lease lost; engine stopped"
        )

    async def _crash_loop(self, account_id: int, task: str) -> None:
        # Статус error аккаунта — с хостом движков; пока движок стоит до перезапуска процесса.
        # Остановку делает цикл движка: здесь — задача самого движка.
        self._halted = f"crash_loop:{task}"
        self._down.set()

    async def _report(self, code: str, text: str) -> None:
        if self._problem != code:
            await self.notifier.notify("error", code, text)
        self._problem = code

    async def _purge_sessions(self) -> None:
        while True:
            await asyncio.sleep(self.session_purge_s)
            try:
                purged = await self.auth.purge_expired()
            except Exception:
                log.exception("expired sessions not purged")
                continue
            if purged:
                log.info("purged %d expired admin sessions", purged)

    async def _retention(self) -> None:
        # Сбой не роняет задачу (иначе супервизор слал бы task_failed на каждом рестарте):
        # одно уведомление на серию неудач, следующая попытка — по расписанию. Политика —
        # из настроек аккаунта в базе: движка в процессе может и не быть.
        failing = False
        await asyncio.sleep(self.retention_first_s)
        while True:
            try:
                settings = DbSettingsStore(self.db, self.config.account_id)
                await settings.load()
                purged = await self.retention.purge(
                    SystemClock().now(), settings.current.retention
                )
            except Exception:
                log.exception("retention failed")
                if not failing:
                    await self.notifier.notify("warn", "retention_failed", "old rows not purged")
                failing = True
            else:
                failing = False
                if any(purged.values()):
                    log.info("retention purged %s", purged)
            await asyncio.sleep(self.retention_s)

    async def stop(self) -> None:
        # Цикл движка — первым: захват и старт не должны начаться посреди остановки.
        with contextlib.suppress(Exception):
            await self.supervisor.cancel("engine")
        runtime, self.account = self.account, None
        self.container.facade = None
        if runtime is not None:
            # Аренда действует — штатная остановка с доработкой конвейера, пока идёт продление;
            # потеряна — аварийная, и освобождение в базу не пишет.
            with contextlib.suppress(Exception):
                await (runtime.stop() if runtime.fence.alive else runtime.abort())
            await self.leases.release(runtime.fence)
        # Продление — после освобождения: отмена запроса по соединению блокировок обрывает
        # соединение вместе со всеми блокировками.
        with contextlib.suppress(Exception):
            await self.supervisor.stop()
        with contextlib.suppress(Exception):
            await self.leases.close()
        with contextlib.suppress(Exception):
            await self.db.dispose()


def create_application(config: AppConfig | None = None) -> FastAPI:
    runtime = Runtime(config or AppConfig())
    app = create_api(runtime.container)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app.router.lifespan_context = lifespan
    app.state.runtime = runtime
    return app
