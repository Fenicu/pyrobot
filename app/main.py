import asyncio
import contextlib
import logging
import os
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
from app.db.retention import DbRetention
from app.db.settings_store import DbSettingsStore
from app.engine.clock import SystemClock
from app.engine.host.account import RuntimeDeps
from app.engine.host.host import EngineHost
from app.engine.host.lease import LeaseManager
from app.engine.lag import LoopLagMonitor

log = logging.getLogger("pyrobot")

SESSION_PURGE_S = 3600.0
# Ретеншн: первый проход не в момент старта (там догон пропусков), дальше — раз в 6 часов.
RETENTION_FIRST_S = 300.0
RETENTION_S = 6 * 3600.0


class Runtime:
    """Процесс: база и пул, вход в админку, `Container` и HTTP, аренды аккаунтов, хост движков
    всех аккаунтов (`EngineHost` — он же реестр движков для API) и задачи процесса на
    супервизоре хоста: продление аренды, монитор задержки цикла, очистка сессий админки,
    ретеншн."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.db = Database(
            config.database_url,
            pool_size=config.db_pool_size,
            max_overflow=config.db_max_overflow,
        )
        self.accounts = AccountRepo(self.db)
        self.auth = AuthRepo(self.db)
        self.lag = LoopLagMonitor()
        self.leases = LeaseManager(self.db, uuid4().hex)
        deps = RuntimeDeps(db=self.db, config=config, accounts=self.accounts, lag=self.lag)
        self.host = EngineHost(
            deps,
            self.leases,
            max_engines=config.max_engines,
            start_gap_s=config.engine_start_gap_s,
        )
        self.supervisor = self.host.supervisor
        self.container = Container(
            config=config,
            auth=self.auth,
            limiter=LoginRateLimiter(),
            db=self.db,
            accounts=self.accounts,
            engines=self.host,
        )
        self.session_purge_s = SESSION_PURGE_S
        self.retention_first_s = RETENTION_FIRST_S
        self.retention_s = RETENTION_S
        # Аккаунты, чей ретеншн не удался: уведомление — одно на серию неудач.
        self._retention_failing: set[int] = set()

    async def start(self) -> None:
        try:
            await self._start()
        except BaseException:
            await self.stop()
            raise

    async def _start(self) -> None:
        if "PYROBOT_ACCOUNT_ID" in os.environ:
            log.warning("PYROBOT_ACCOUNT_ID больше не читается")
        password = self.config.admin_password
        await self.auth.ensure_admin(
            self.config.admin_login, password.get_secret_value() if password else None
        )
        await self.leases.open()
        self.supervisor.start("lease", self.leases.run)
        self.supervisor.start("lag", self.lag.run)
        self.supervisor.start("session-purge", self._purge_sessions)
        self.supervisor.start("retention", self._retention)
        # Движки включённых аккаунтов — до готовности HTTP, как и прежде.
        await self.host.start()

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
        await asyncio.sleep(self.retention_first_s)
        while True:
            await self._retention_pass()
            await asyncio.sleep(self.retention_s)

    async def _retention_pass(self) -> None:
        """Все аккаунты, кроме удаляемых, — включая выключенные и упавшие, — каждый по своей
        политике из настроек в базе: движка в процессе у аккаунта может и не быть. Сбой не
        роняет задачу (иначе супервизор перезапускал бы её с начальной паузой): одно уведомление
        аккаунту на серию неудач, следующая попытка — по расписанию."""
        try:
            accounts = await self.accounts.with_status("enabled", "disabled", "error")
        except Exception:
            log.exception("retention failed: accounts not listed")
            return
        for account in accounts:
            try:
                settings = DbSettingsStore(self.db, account.id)
                await settings.load()
                purged = await DbRetention(self.db, account.id).purge(
                    SystemClock().now(), settings.current.retention
                )
            except Exception:
                log.exception("retention of account %d failed", account.id)
                if account.id not in self._retention_failing:
                    await DbNotifier(self.db, account.id).notify(
                        "warn", "retention_failed", "old rows not purged"
                    )
                self._retention_failing.add(account.id)
            else:
                self._retention_failing.discard(account.id)
                if any(purged.values()):
                    log.info("retention of account %d purged %s", account.id, purged)

    async def stop(self) -> None:
        # Хост — первым: сверка, затем движки (штатно, пока аренда действует, иначе аварийно) и
        # освобождение их аренд.
        with contextlib.suppress(Exception):
            await self.host.stop()
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
