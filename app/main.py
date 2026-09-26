import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import timedelta

from fastapi import FastAPI

from app.api.app import create_api
from app.api.container import Container
from app.api.security import LoginRateLimiter
from app.config import AppConfig
from app.db.actions import DbActionStore
from app.db.auth_repo import AuthRepo
from app.db.base import Database
from app.db.journal import DbJournal
from app.db.lock import SingleInstanceLock
from app.db.notifications import DbNotifier
from app.db.settings_store import DbSettingsStore
from app.engine.bus import Bus
from app.engine.clock import SystemClock
from app.engine.facade import EngineFacade
from app.engine.gateway.gateway import ActionGateway
from app.engine.lag import LoopLagMonitor
from app.engine.parsing import default_parser
from app.engine.pipeline import NullReducer, Pipeline
from app.engine.supervisor import Supervisor
from app.engine.tg_auth import TgAuthBackend, TgAuthManager
from app.engine.transport.base import Transport
from app.engine.transport.fake import FakeTgBackend, FakeTransport
from app.engine.transport.kurigram import ChatFilter, KurigramTransport

log = logging.getLogger("pyrobot")
LOCK_CHECK_S = 10.0


class Runtime:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.db = Database(config.database_url)
        self.notifier = DbNotifier(self.db, config.account_id)
        self.settings = DbSettingsStore(self.db, config.account_id)
        self.lock = SingleInstanceLock(self.db)
        self.auth = AuthRepo(self.db)
        self.container = Container(config=config, auth=self.auth, limiter=LoginRateLimiter())
        self.supervisor = Supervisor(self.notifier)
        self.pipeline: Pipeline | None = None
        self.gateway: ActionGateway | None = None
        self.tg: TgAuthManager | None = None
        self.facade: EngineFacade | None = None
        self.transport: Transport | None = None
        self._kurigram: KurigramTransport | None = None

    async def start(self) -> None:
        try:
            await self._start()
        except BaseException:
            await self.stop()
            raise

    async def _start(self) -> None:
        await self.settings.load()
        password = self.config.admin_password
        await self.auth.ensure_admin(
            self.config.admin_login, password.get_secret_value() if password else None
        )
        if not await self.lock.acquire():
            await self.notifier.notify(
                "error", "second_instance", "another pyrobot instance holds the lock"
            )
            return
        actions = DbActionStore(self.db, self.config.account_id)
        unknown = await actions.mark_unfinished_unknown()
        bus = Bus()
        react_age = self.settings.current.engine.recovered_react_max_age_min
        self.pipeline = Pipeline(
            journal=DbJournal(self.db, self.config.account_id),
            parser=default_parser(),
            reducer=NullReducer(),
            bus=bus,
            react_max_age=timedelta(minutes=react_age),
        )
        await self.pipeline.load()
        transport, backend = self._make_transport(self.pipeline)
        self.transport = transport
        pipeline = self.pipeline
        self.gateway = ActionGateway(
            transport=transport,
            store=actions,
            settings=self.settings,
            latest=pipeline.latest,
            boundary=lambda: pipeline.last_journal_id,
            clock=SystemClock(),
        )
        if unknown:
            self.gateway.block_spending("reconcile_required")
            await self.notifier.notify(
                "warn",
                "actions_outcome_unknown",
                f"{len(unknown)} actions interrupted by restart",
            )
        bus.subscribe(self.gateway.on_delivery, priority=0)
        self.tg = TgAuthManager(
            backend, expected_user_id=self.settings.current.telegram.expected_user_id
        )
        if self._kurigram is not None:
            self._kurigram.on_auth_lost = self.tg.mark_lost
        lag = LoopLagMonitor()
        lock = self.lock
        self.facade = EngineFacade(
            settings=self.settings,
            gateway=self.gateway,
            pipeline=pipeline,
            tg_auth=self.tg,
            lag=lag,
            lock_ok=lambda: lock.held,
            workers_ok=self.supervisor.healthy,
        )
        self.container.facade = self.facade
        self.supervisor.start("pipeline", pipeline.run)
        self.supervisor.start("gateway", self.gateway.run)
        self.supervisor.start("lag", lag.run)
        self.supervisor.start("lock-watch", self._watch_lock)
        await self.tg.boot()

    def _make_transport(self, pipeline: Pipeline) -> tuple[Transport, TgAuthBackend]:
        if self.config.transport == "fake":
            return FakeTransport(), FakeTgBackend(authorized=False)
        kurigram = KurigramTransport(
            api_id=self.config.tg_api_id,
            api_hash=self.config.tg_api_hash.get_secret_value(),
            workdir=self.config.data_dir,
            chat_filter=ChatFilter.from_settings(self.settings.current.chats),
            sink=pipeline.submit,
        )
        self._kurigram = kurigram
        return kurigram, kurigram

    async def _watch_lock(self) -> None:
        while True:
            await asyncio.sleep(LOCK_CHECK_S)
            if not await self.lock.check():
                if self.gateway is not None:
                    await self.gateway.kill("lock_lost")
                await self.notifier.notify(
                    "error", "lock_lost", "single-instance lock lost; sending stopped"
                )
                return

    async def stop(self) -> None:
        if self.gateway is not None:
            with contextlib.suppress(Exception):
                await self.gateway.shutdown()
        with contextlib.suppress(Exception):
            await self.supervisor.stop()
        if self._kurigram is not None:
            with contextlib.suppress(Exception):
                await self._kurigram.stop()
        with contextlib.suppress(Exception):
            await self.lock.release()
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
