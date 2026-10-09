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
from app.db.audit import AuditLog
from app.db.auth_repo import AuthRepo
from app.db.base import Database
from app.db.crypto import SecretBox, SecretKeyError, derive_key, ensure_key, parse_key
from app.db.invites import InviteRepo
from app.db.notifications import DbNotifier, ServerNotifier
from app.db.recovery import RecoveryCodes, RecoveryRequests
from app.db.retention import DbRetention
from app.db.server_settings import ServerSettingsRepo
from app.db.users import UserRepo
from app.engine.clock import SystemClock
from app.engine.host.account import RuntimeDeps
from app.engine.host.codes import CodeLimiter
from app.engine.host.host import EngineHost
from app.engine.host.lease import LeaseManager
from app.engine.lag import LoopLagMonitor
from app.engine.transport.kurigram import logout_offline
from app.memwatch import MemWatch, load_malloc_trim

log = logging.getLogger("pyrobot")

SESSION_PURGE_S = 3600.0
# Ретеншн: первый проход не в момент старта (там догон пропусков), дальше — раз в 6 часов.
RETENTION_FIRST_S = 300.0
RETENTION_S = 6 * 3600.0


def _key_text(config: AppConfig) -> str | None:
    key = config.secret_key
    return None if key is None else key.get_secret_value()


def _secret_box(config: AppConfig) -> SecretBox | None:
    """Ключ сессий Telegram; не задан или испорчен — `None`: отказ старта — в `_check_key`."""
    try:
        return SecretBox(parse_key(_key_text(config)))
    except SecretKeyError:
        return None


class Runtime:
    """Процесс: база и пул, вход в админку, `Container` и HTTP, аренды аккаунтов, хост движков
    всех аккаунтов (`EngineHost` — он же реестр движков для API) и задачи процесса на
    супервизоре хоста: продление аренды, монитор задержки цикла, очистка сессий админки,
    ретеншн, замеры памяти."""

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
        self.memwatch = MemWatch(trim_fn=load_malloc_trim())
        self.leases = LeaseManager(self.db, uuid4().hex)
        self.box = _secret_box(config)
        self.server_notifier = ServerNotifier(self.db)
        self.audit = AuditLog(self.db)
        self.server_settings = ServerSettingsRepo(self.db, self.audit)
        self._host_tasks: set[asyncio.Task[None]] = set()
        codes = CodeLimiter(
            lambda: (
                self.server_settings.current.limits.tg_codes_per_hour,
                self.server_settings.current.limits.tg_codes_per_account_hour,
            ),
            on_host_limit=self._on_tg_codes_limit,
        )
        deps = RuntimeDeps(
            db=self.db,
            config=config,
            accounts=self.accounts,
            lag=self.lag,
            codes=codes,
            box=self.box,
            server=self.server_settings,
            server_notifier=self.server_notifier,
        )
        self.host = EngineHost(
            deps,
            self.leases,
            max_engines=config.max_engines,
            start_gap_s=config.engine_start_gap_s,
            logout_offline=self._logout_offline,
            server=self.server_notifier,
        )
        self.leases.on_connection_lost = self._on_lock_connection_lost
        self.supervisor = self.host.supervisor
        self.users = UserRepo(self.db)
        self.invites = InviteRepo(self.db, self.audit)
        self.recovery_codes = RecoveryCodes(self.db)
        key_text = _key_text(config)
        if key_text:
            try:
                raw_key = parse_key(key_text)
                self.recovery_key = derive_key(raw_key, "recovery")
            except SecretKeyError:
                self.recovery_key = os.urandom(32)
        else:
            self.recovery_key = os.urandom(32)
        self.recovery_requests = RecoveryRequests(self.db, self.recovery_key)
        self.container = Container(
            config=config,
            auth=self.auth,
            limiter=LoginRateLimiter(),
            db=self.db,
            accounts=self.accounts,
            engines=self.host,
            users=self.users,
            server_settings=self.server_settings,
            invites=self.invites,
            recovery_codes=self.recovery_codes,
            audit=self.audit,
            server_notifier=self.server_notifier,
            recovery_key=self.recovery_key,
            recovery_requests=self.recovery_requests,
            box=self.box,
            memwatch=self.memwatch,
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
        await self._check_key()
        await self.server_settings.load()
        password = self.config.admin_password
        await self.auth.ensure_owner(
            self.config.admin_login, password.get_secret_value() if password else None
        )
        await self.leases.open()
        self.supervisor.start("lease", self.leases.run)
        self.supervisor.start("lag", self.lag.run)
        self.supervisor.start("session-purge", self._purge_sessions)
        self.supervisor.start("retention", self._retention)
        self.supervisor.start("memwatch", self.memwatch.run)
        # Движки включённых аккаунтов хост поднимает в фоне: HTTP и /readyz готовы сразу.
        await self.host.start()

    async def _check_key(self) -> None:
        """Ключ сессий Telegram (раздел 4.3 спеки) обязателен для kurigram; транспорту fake без
        ключа сессии не нужны, но заданный ключ сверяется с базой всегда. Ключ не задан, испорчен
        или не подходит к базе — ошибка в лог и отказ старта; сессии удаляет только осознанный
        сброс `PYROBOT_SECRET_KEY_RESET=1`."""
        if self.config.secret_key is None and self.config.transport == "fake":
            return
        try:
            box = SecretBox(parse_key(_key_text(self.config)))
            await ensure_key(self.db, box, reset=self.config.secret_key_reset)
        except SecretKeyError as exc:
            log.error("процесс не стартует: %s", exc)
            raise

    def _on_tg_codes_limit(self) -> None:
        async def _notify() -> None:
            await self.server_notifier.notify(
                "warn", "tg_codes_limit", "telegram login codes host limit reached"
            )

        task = asyncio.create_task(_notify())
        self._host_tasks.add(task)
        task.add_done_callback(self._host_tasks.discard)

    async def _on_lock_connection_lost(self) -> None:
        await self.server_notifier.notify(
            "error", "lock_connection_lost", "lease lock connection lost"
        )

    async def _logout_offline(self, account_id: int) -> None:
        """Выход из Telegram удаляемого аккаунта без движка; у транспорта fake Telegram нет."""
        if self.config.transport == "kurigram" and self.box is not None:
            await logout_offline(self.db, self.box, self.config, account_id)

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
        """Все аккаунты, кроме удаляемых, — включая выключенные и упавшие, — по политике сервера:
        движка в процессе у аккаунта может и не быть. Сбой не роняет задачу (иначе супервизор
        перезапускал бы её с начальной паузой): одно уведомление аккаунту на серию неудач,
        следующая попытка — по расписанию."""
        policy = self.server_settings.current.retention
        now = SystemClock().now()
        try:
            accounts = await self.accounts.with_status("enabled", "disabled", "error")
        except Exception:
            log.exception("retention failed: accounts not listed")
            return
        for account in accounts:
            try:
                purged = await DbRetention(self.db, account.id).purge(now, policy)
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
        try:
            purged_server = await DbRetention(self.db, 1).purge_server(now, policy)
            if any(purged_server.values()):
                log.info("retention of server purged %s", purged_server)
        except Exception:
            log.exception("retention of server failed")

    async def stop(self) -> None:
        for task in list(self._host_tasks):
            task.cancel()
        with contextlib.suppress(Exception):
            await self.container.drain()
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
