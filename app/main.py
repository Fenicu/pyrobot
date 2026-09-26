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
from app.db.metro import DbMetroRunStore
from app.db.notifications import DbNotifier
from app.db.planner import DbPlannerStore
from app.db.settings_store import DbSettingsStore
from app.engine.bus import Bus
from app.engine.clock import SystemClock
from app.engine.facade import EngineFacade
from app.engine.gateway.gateway import RECONCILE_REASON, ActionGateway
from app.engine.lag import LoopLagMonitor
from app.engine.parsing import default_parser
from app.engine.pipeline import Pipeline
from app.engine.planner.loop import PlannerLoop
from app.engine.reconcile import Reconciler
from app.engine.state.model import load_state
from app.engine.state.reducer import StateReducer
from app.engine.supervisor import Supervisor
from app.engine.tg_auth import TgAuthBackend, TgAuthManager, TgState
from app.engine.transport.base import Transport
from app.engine.transport.fake import FakeTgBackend, FakeTransport
from app.engine.transport.kurigram import ChatFilter, KurigramTransport
from app.engine.unrecognized import UnrecognizedWatch

log = logging.getLogger("pyrobot")
LOCK_CHECK_S = 10.0
TG_PROBE_S = 60.0
PIPELINE_DRAIN_S = 10.0
SESSION_PURGE_S = 3600.0
RECONCILE_POLL_S = 5.0
PLANNER_POLL_S = 5.0


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
        self.lock_check_s = LOCK_CHECK_S
        self.tg_probe_s = TG_PROBE_S
        self.session_purge_s = SESSION_PURGE_S
        self.reconcile_poll_s = RECONCILE_POLL_S
        self.planner_poll_s = PLANNER_POLL_S
        self.pipeline: Pipeline | None = None
        self.gateway: ActionGateway | None = None
        self.tg: TgAuthManager | None = None
        self.facade: EngineFacade | None = None
        self.planner: PlannerLoop | None = None
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
        await actions.mark_unfinished_unknown()
        bus = Bus()
        react_age = self.settings.current.engine.recovered_react_max_age_min
        reducer = StateReducer()
        self.pipeline = Pipeline(
            journal=DbJournal(self.db, self.config.account_id),
            parser=default_parser(self.settings.current.chats),
            reducer=reducer,
            metrics=reducer.metrics,
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
            can_send=self._can_send,
        )
        pending = await actions.unreconciled()
        if pending:
            self.gateway.block_spending(RECONCILE_REASON)
            await self.notifier.notify(
                "warn",
                "actions_outcome_unknown",
                f"{len(pending)} actions need state reconciliation",
            )
        bus.subscribe(self.gateway.on_delivery, priority=0)
        bus.subscribe(UnrecognizedWatch(self.notifier, SystemClock()).on_delivery, priority=50)
        self.tg = TgAuthManager(
            backend,
            expected_user_id=self.settings.current.telegram.expected_user_id,
            notifier=self.notifier,
        )
        if self._kurigram is not None:
            self._kurigram.on_auth_lost = self.tg.mark_lost
        settings = self.settings
        gateway = self.gateway
        reconciler = Reconciler(
            gateway=gateway,
            store=actions,
            state=lambda: pipeline.state,
            notifier=self.notifier,
            settings=settings,
            clock=SystemClock(),
            ready=lambda: (
                self._can_send() is None
                and gateway.kill_reason is None
                and not settings.current.engine.killed
            ),
            game_chat_id=settings.current.chats.game_chat_id,
            poll_s=self.reconcile_poll_s,
        )
        gateway.on_uncertain = reconciler.note
        planner_store = DbPlannerStore(self.db, self.config.account_id)
        interrupted = await planner_store.close_running(SystemClock().now())
        if interrupted:
            log.info("marked %d unfinished scenario runs as interrupted", interrupted)
        self.planner = PlannerLoop(
            gateway=gateway,
            state=lambda: load_state(pipeline.state),
            settings=settings,
            clock=SystemClock(),
            store=planner_store,
            notifier=self.notifier,
            ready=self._planner_ready,
            poll_s=self.planner_poll_s,
            metro_store=DbMetroRunStore(self.db, self.config.account_id),
        )
        bus.subscribe(self.planner.on_delivery, priority=90)
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
            notifier=self.notifier,
            reconciler=reconciler,
            planner=self.planner,
        )
        self.container.facade = self.facade
        self.supervisor.start("pipeline", pipeline.run)
        self.supervisor.start("gateway", self.gateway.run)
        self.supervisor.start("reconcile", reconciler.run)
        if self.config.planner:
            self.supervisor.start("planner", self.planner.run)
        self.supervisor.start("lag", lag.run)
        self.supervisor.start("lock-watch", self._watch_lock)
        self.supervisor.start("session-purge", self._purge_sessions)
        if self._kurigram is not None:
            self.supervisor.start("tg-probe", self._probe_tg)
        await self.tg.boot()

    def _can_send(self) -> str | None:
        if not self.lock.held:
            return "lock_lost"
        if self.tg is None or self.tg.status().state is not TgState.ONLINE:
            return "tg_offline"
        return None

    def _planner_ready(self) -> str | None:
        engine = self.settings.current.engine
        if engine.paused:
            return "paused"
        gateway = self.gateway
        if engine.killed or (gateway is not None and gateway.kill_reason is not None):
            return "killed"
        if gateway is not None and gateway.spending_blocked is not None:
            return "spending_blocked"
        if self.pipeline is not None and not self.pipeline.healthy:
            return "pipeline_unhealthy"
        return self._can_send()

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
        # После потери лока не выходим (Supervisor трактует выход как штатное
        # завершение и перезапускает задачу) — паркуемся, чтобы kill/notify
        # сработали ровно один раз, без ретриггера по backoff.
        while self.lock.held:
            await asyncio.sleep(self.lock_check_s)
            if not await self.lock.check():
                if self.gateway is not None:
                    await self.gateway.kill("lock_lost")
                await self.notifier.notify(
                    "error", "lock_lost", "single-instance lock lost; sending stopped"
                )
        await asyncio.Event().wait()

    async def _probe_tg(self) -> None:
        while True:
            await asyncio.sleep(self.tg_probe_s)
            kurigram, tg = self._kurigram, self.tg
            if kurigram is not None and tg is not None and tg.status().state is TgState.ONLINE:
                await kurigram.probe()

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

    async def stop(self) -> None:
        # Планировщик — первым: новый шаг сценария не должен уйти в закрывающийся шлюз.
        with contextlib.suppress(Exception):
            await self.supervisor.cancel("planner")
        if self.gateway is not None:
            with contextlib.suppress(Exception):
                await self.gateway.shutdown()
        if self._kurigram is not None:
            with contextlib.suppress(Exception):
                await self._kurigram.stop()
        # Конвейер останавливается после транспорта: всё, что успело прийти, попадает в журнал.
        if self.pipeline is not None:
            with contextlib.suppress(Exception):
                if not await self.pipeline.drain(PIPELINE_DRAIN_S):
                    log.warning(
                        "pipeline not drained on stop, %d messages left",
                        self.pipeline.unfinished,
                    )
        with contextlib.suppress(Exception):
            await self.supervisor.stop()
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
