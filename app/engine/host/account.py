"""Движок одного аккаунта (раздел 4.2 спеки): хранилища аккаунта под оградой его аренды, свой
поток событий, конвейер, транспорт, шлюз, вход в Telegram, сверка, сверка истории, планировщик,
реакции, пересылка в чат команды, фасад и свой супервизор. Процесс создаёт движок после захвата
аренды."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.server_settings import ServerSettingsRepo
    from app.db.tg_storage import PgSessionStorage

from app.config import AppConfig
from app.db.accounts import AccountInfo, AccountRepo
from app.db.actions import DbActionStore
from app.db.base import Database
from app.db.chat_marks import ChatMarks
from app.db.crypto import SecretBox, Undecryptable
from app.db.journal import DbJournal
from app.db.metro import DbMetroRunStore
from app.db.models import NotificationRow
from app.db.notifications import DbNotifier
from app.db.planner import DbPlannerStore
from app.db.settings_store import DbSettingsStore
from app.engine.artifact import ArtifactRuns
from app.engine.bus import Bus
from app.engine.clock import SystemClock
from app.engine.facade import EngineFacade
from app.engine.fence import Fence
from app.engine.gadgets import GadgetRuns
from app.engine.gateway.gateway import RECONCILE_REASON, ActionGateway
from app.engine.host.codes import CodeLimiter
from app.engine.lag import LoopLagMonitor
from app.engine.notify import NotifierPort
from app.engine.parsing import default_parser
from app.engine.parsing.sleep import RobberyAlert
from app.engine.pipeline import Pipeline
from app.engine.planner.loop import PlannerLoop
from app.engine.reactions import RobberyDefense
from app.engine.reconcile import Reconciler
from app.engine.scenarios.context import History, Reread
from app.engine.settings import Settings, self_chat_fields
from app.engine.state.model import company_of, load_state
from app.engine.state.reducer import StateReducer
from app.engine.stream import (
    EventStream,
    PublishingActionStore,
    PublishingPlannerStore,
    StreamFeed,
)
from app.engine.supervisor import Supervisor
from app.engine.team_forward import TeamForward
from app.engine.tg_auth import TgAuthBackend, TgAuthManager, TgState
from app.engine.transport.base import Transport
from app.engine.transport.fake import FakeTgBackend, FakeTransport
from app.engine.transport.history import HistorySync, readers_for
from app.engine.transport.kurigram import ChatFilter, KurigramTransport, load_tg_app, session_peers
from app.engine.types import IncomingMessage
from app.engine.unrecognized import UnrecognizedWatch
from app.logctx import current_account

if TYPE_CHECKING:
    # pyrogram (его импортирует `tg_storage`) — лениво, внутри работающего цикла.
    from app.db.tg_storage import PgSessionStorage

log = logging.getLogger(__name__)
# Файл сессии установки до мультиаккаунта в каталоге данных: его сессию перенимает аккаунт 1.
LEGACY_SESSION_FILE = "pyrobot.session"
REREAD_DRAIN_S = 10.0
TG_PROBE_S = 60.0
PIPELINE_DRAIN_S = 10.0
HISTORY_DRAIN_S = 60.0
RECONCILE_POLL_S = 5.0
PLANNER_POLL_S = 5.0


def journal_history(journal: DbJournal) -> History:
    """Все записанные правки сообщения — по ним восстанавливается карта забега метро."""

    async def history(chat_id: int, msg_id: int) -> list[IncomingMessage]:
        return await journal.revisions(chat_id, msg_id)

    return history


def live_reread(transport: Transport, pipeline: Pipeline) -> Reread:
    """Текущая версия сообщения из Telegram через конвейер: журнал и состояние её учтут, а она
    станет текущей ревизией — после рестарта кэш ревизий пуст, а шлюз кликает только по кнопкам
    последней ревизии."""

    async def reread(chat_id: int, msg_id: int) -> IncomingMessage | None:
        try:
            msg = await transport.fetch(chat_id, msg_id)
        except Exception:
            log.exception("message %s/%s not reread", chat_id, msg_id)
            return None
        if msg is None:
            return None
        await pipeline.submit(msg)
        await pipeline.drain(REREAD_DRAIN_S)
        pipeline.prime(msg)
        return msg

    return reread


def pipeline_deliver(pipeline: Pipeline) -> Callable[[list[IncomingMessage]], Awaitable[bool]]:
    """Сообщения прохода сверки истории — в конвейер; проход удачен, когда конвейер их записал
    (`drain`), — только тогда сверка двигает отметку."""

    async def deliver(messages: list[IncomingMessage]) -> bool:
        for msg in messages:
            await pipeline.submit(msg)
        return await pipeline.drain(HISTORY_DRAIN_S)

    return deliver


@contextlib.contextmanager
def _in_account(account_id: int) -> Iterator[None]:
    """Аккаунт в контексте: его получают строки лога и задачи, созданные внутри."""
    token = current_account.set(account_id)
    try:
        yield
    finally:
        current_account.reset(token)


@dataclass(frozen=True)
class RuntimeDeps:
    """Общее для движков процесса."""

    db: Database
    config: AppConfig
    accounts: AccountRepo
    lag: LoopLagMonitor
    # Лимит запросов кода входа в Telegram — общий для движков процесса.
    codes: CodeLimiter
    # Ключ сессий Telegram в базе; нет только у транспорта fake без `PYROBOT_SECRET_KEY`.
    box: SecretBox | None = None
    server: ServerSettingsRepo | None = None
    # Уведомления сервера (владельцам): неиспользованный код входа через серверное приложение.
    server_notifier: NotifierPort | None = None


class AccountRuntime:
    """Движок аккаунта `account` на ограде его аренды `fence`: все пишущие транзакции
    хранилищ аккаунта идут через неё. Серия сбоев задачи — `on_crash_loop(аккаунт, задача)`."""

    def __init__(
        self,
        account: AccountInfo,
        deps: RuntimeDeps,
        fence: Fence,
        *,
        on_crash_loop: Callable[[int, str], Awaitable[None]],
    ) -> None:
        self.account_id = account.id
        self.generation = account.engine_generation
        self.fence = fence
        self._account = account
        self._deps = deps
        self._on_crash_loop = on_crash_loop
        db = deps.db
        self.notifier = DbNotifier(db, account.id, fence=fence)
        self.settings = DbSettingsStore(db, account.id, fence=fence)
        self.stream = EventStream()
        self.notifier.listeners.append(self._publish_notification)
        self.settings.listeners.append(self._publish_settings)
        self.supervisor = Supervisor(self.notifier, on_crash_loop=self._crash_loop)
        self.tg_probe_s = TG_PROBE_S
        self.reconcile_poll_s = RECONCILE_POLL_S
        self.planner_poll_s = PLANNER_POLL_S
        self.pipeline: Pipeline | None = None
        self.gateway: ActionGateway | None = None
        self.tg: TgAuthManager | None = None
        self.facade: EngineFacade | None = None
        self.planner: PlannerLoop | None = None
        self.reactions: RobberyDefense | None = None
        self.team_forward: TeamForward | None = None
        self.transport: Transport | None = None
        self.history: HistorySync | None = None
        self._kurigram: KurigramTransport | None = None

    async def start(self) -> None:
        """Настройки читаются здесь, после захвата аренды. Сбой старта останавливает то, что
        успело подняться."""
        with _in_account(self.account_id):
            try:
                await self._start()
            except BaseException:
                await (self.stop() if self.fence.alive else self.abort())
                raise

    async def _start(self) -> None:
        config = self._deps.config
        db = self._deps.db
        await self.settings.load()
        server = self._deps.server
        if server is not None:
            bounds = server.current.engine_bounds
            clamped = bounds.clamp(self.settings.current)
            if clamped != self.settings.current:
                await self.settings.update(
                    lambda _: bounds.clamp(self.settings.current),
                    changed_by="system",
                    expected_version=None,
                )
        # Настройки, с которыми движок запущен: по ним — фильтр чатов, разбор и сверка истории.
        started = self.settings.current
        actions = PublishingActionStore(
            DbActionStore(db, self.account_id, fence=self.fence), self.stream
        )
        # Прерванные пересылки уведомляются в той же транзакции, что и закрытие строк.
        await actions.mark_unfinished_unknown()
        bus = Bus()
        react_age = self.settings.current.engine.recovered_react_max_age_min
        reducer = StateReducer()
        journal = DbJournal(db, self.account_id, fence=self.fence)
        self.pipeline = Pipeline(
            journal=journal,
            parser=default_parser(self.settings.current.chats),
            reducer=reducer,
            metrics=reducer.metrics,
            bus=bus,
            react_max_age=timedelta(minutes=react_age),
        )
        await self.pipeline.load()
        transport, backend = await self._make_transport(self.pipeline)
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
            state_version=lambda: pipeline.version,
            own_company=lambda: company_of(pipeline.state),
            reread=live_reread(transport, pipeline),
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
            expected_user_id=self._account.tg_user_id,
            bind=self._bind_telegram,
            self_chat=lambda user_id: self._self_chat(started, user_id),
            codes=self._deps.codes,
            account_id=self.account_id,
            notifier=self.notifier,
            server_notifier=self._deps.server_notifier,
            server_app=self._server_app,
        )
        if self._kurigram is not None:
            self._kurigram.on_auth_lost = self.tg.mark_lost
            self._kurigram.on_overload = self.tg.mark_overload
            self._kurigram.on_resumed = self.tg.mark_resumed
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
            reread=live_reread(transport, pipeline),
        )
        gateway.on_uncertain = reconciler.note
        planner_store = PublishingPlannerStore(
            DbPlannerStore(db, self.account_id, fence=self.fence), self.stream
        )
        interrupted = await planner_store.close_running(SystemClock().now())
        if interrupted:
            log.info("marked %d unfinished scenario runs as interrupted", interrupted)
        artifacts = ArtifactRuns(
            settings=settings,
            state=lambda: load_state(pipeline.state),
            notifier=self.notifier,
            clock=SystemClock(),
        )
        # Один на движок: цикл ведёт задачу заточки, фасад запускает и останавливает её.
        gadgets = GadgetRuns(
            settings=settings,
            state=lambda: load_state(pipeline.state),
            notifier=self.notifier,
            clock=SystemClock(),
        )
        self.planner = PlannerLoop(
            gateway=gateway,
            state=lambda: load_state(pipeline.state),
            settings=settings,
            clock=SystemClock(),
            store=planner_store,
            notifier=self.notifier,
            ready=self._planner_ready,
            poll_s=self.planner_poll_s,
            metro_store=DbMetroRunStore(db, self.account_id, fence=self.fence),
            history=journal_history(journal),
            reread=live_reread(transport, pipeline),
            auto=config.planner,
            artifacts=artifacts,
            gadgets=gadgets,
            publish=self.stream.publish,
        )
        game_chat = settings.current.chats.game_chat_id

        async def journaled_alerts(since: datetime) -> list[IncomingMessage]:
            return await journal.messages_with_event(game_chat, RobberyAlert.kind, since)

        self.reactions = RobberyDefense(
            gateway=gateway,
            settings=settings,
            reread=live_reread(transport, pipeline),
            notifier=self.notifier,
            alerts=journaled_alerts,
            ready=lambda: self._can_send() is None,
        )
        bus.subscribe(self.reactions.on_delivery, priority=20)
        self.team_forward = TeamForward(
            gateway=gateway, settings=settings, notifier=self.notifier, clock=SystemClock()
        )
        bus.subscribe(self.team_forward.on_delivery, priority=30)
        bus.subscribe(self.planner.on_delivery, priority=90)
        bus.subscribe(StreamFeed(self.stream, lambda: pipeline.state).on_delivery, priority=95)
        fence = self.fence
        server = self._deps.server
        bounds_fn = (lambda: server.current.engine_bounds) if server is not None else None
        self.facade = EngineFacade(
            settings=self.settings,
            gateway=self.gateway,
            pipeline=pipeline,
            tg_auth=self.tg,
            lease_ok=lambda: fence.alive,
            workers_ok=self.supervisor.healthy,
            notifier=self.notifier,
            reconciler=reconciler,
            planner=self.planner,
            stream=self.stream,
            transport=transport,
            history=lambda: self.history,
            artifacts=artifacts,
            gadgets=gadgets,
            bounds=bounds_fn,
        )
        self.supervisor.start("pipeline", pipeline.run)
        self.supervisor.start("gateway", self.gateway.run)
        self.supervisor.start("reconcile", reconciler.run)
        self.supervisor.start("reactions", self.reactions.run)
        self.supervisor.start("team-forward", self.team_forward.run)
        # Без PYROBOT_PLANNER цикл всё равно нужен: он исполняет ручные запуски сценариев.
        self.supervisor.start("planner", self.planner.run)
        if self._kurigram is not None:
            self.supervisor.start("tg-probe", self._probe_tg)
            await self._start_history(self._kurigram, journal, pipeline)
        await self.tg.boot()

    async def _start_history(
        self, kurigram: KurigramTransport, journal: DbJournal, pipeline: Pipeline
    ) -> None:
        """Сверка истории (раздел 4.3 спеки): отметки чтений, которых больше нет в настройках,
        удаляются; проходы идут задачей `history` от выхода в онлайн — старт движка их не ждёт."""
        chats = self.settings.current.chats
        readers = readers_for(chats)
        marks = ChatMarks(self._deps.db, self.account_id, self.fence)
        pruned = await marks.prune(readers)
        if pruned:
            log.info("history marks of %d removed chat readers deleted", pruned)
        self.history = HistorySync(
            source=kurigram,
            marks=marks,
            known=journal.known,
            deliver=pipeline_deliver(pipeline),
            accepts=ChatFilter.from_settings(chats).accepts,
            notifier=self.notifier,
            readers=readers,
            online=lambda: kurigram.online,
            swinfo=(chats.swinfo_chat_id, chats.swinfo_user_id),
        )
        kurigram.on_history_needed = self.history.request
        self.supervisor.start("history", self.history.run)

    async def log_out(self) -> None:
        """Выход из Telegram перед штатной остановкой удаляемого аккаунта: сессия закрывается
        и у Telegram. Вызов идёт через ограду аренды."""
        with _in_account(self.account_id):
            if self.tg is not None:
                await self.fence.call(self.tg.logout)

    async def stop(self) -> None:
        """Штатная остановка: планировщик, сверка истории, шлюз, транспорт, доработка конвейера,
        задачи."""
        with _in_account(self.account_id):
            # Планировщик — первым: новый шаг сценария не должен уйти в закрывающийся шлюз.
            with contextlib.suppress(Exception):
                await self.supervisor.cancel("planner")
            # Сверка истории — до транспорта: проход не читает из закрывающегося клиента; что
            # она уже передала, конвейер дорабатывает.
            with contextlib.suppress(Exception):
                await self.supervisor.cancel("history")
            if self.gateway is not None:
                with contextlib.suppress(Exception):
                    await self.gateway.shutdown()
            if self._kurigram is not None:
                with contextlib.suppress(Exception):
                    await self._kurigram.stop()
            # Конвейер — после транспорта: всё, что успело прийти, попадает в журнал.
            if self.pipeline is not None:
                with contextlib.suppress(Exception):
                    if not await self.pipeline.drain(PIPELINE_DRAIN_S):
                        log.warning(
                            "pipeline not drained on stop, %d messages left",
                            self.pipeline.unfinished,
                        )
            with contextlib.suppress(Exception):
                await self.supervisor.stop()
            if self.tg is not None:
                await self.tg.close()
            # Потоки SSE этого движка заканчиваются: клиенты переподключатся к новому.
            self.stream.close()

    async def abort(self) -> None:
        """Аварийная остановка (аренда потеряна): без доработки конвейера и финальных записей
        — после срока движок в базу не пишет; что не дошло до журнала, вернёт сверка истории.
        Транспорт закрывается аварийно (`KurigramTransport.abort`: очередь kurigram не
        дорабатывается), затем отменяются задачи."""
        with _in_account(self.account_id):
            if self._kurigram is not None:
                with contextlib.suppress(Exception):
                    await self._kurigram.abort()
            with contextlib.suppress(Exception):
                await self.supervisor.stop()
            if self.tg is not None:
                await self.tg.close()
            self.stream.close()

    def _server_app(self) -> bool:
        """Вход идёт через серверное приложение Telegram (не своё приложение аккаунта)."""
        kurigram = self._kurigram
        return kurigram is not None and kurigram.api_id == self._deps.config.tg_api_id

    async def _crash_loop(self, task: str) -> None:
        await self._on_crash_loop(self.account_id, task)

    def _publish_notification(self, row: NotificationRow) -> None:
        self.stream.publish(
            "notification", {"id": row.id, "level": row.level, "code": row.code, "text": row.text}
        )

    async def _bind_telegram(self, user_id: int) -> int:
        bound = await self._deps.accounts.bind_telegram(self.account_id, user_id)
        if bound == user_id:
            log.info("telegram account %d bound", user_id)
        return bound

    def _self_chat(self, started: Settings, user_id: int) -> list[str]:
        """Поля `chats.*`, равные пользователю Telegram, — в текущих настройках и в тех, с
        которыми движок запущен: исправленные правкой чаты вступают в силу только перезапуском
        аккаунта, а до него фильтр пускал бы «Избранное» в журнал."""
        current = self_chat_fields(self.settings.current, user_id)
        return list(dict.fromkeys(self_chat_fields(started, user_id) + current))

    def _publish_settings(self, settings: Settings, version: int) -> None:
        engine = settings.engine
        self.stream.publish(
            "settings",
            {
                "version": version,
                "mode": engine.mode,
                "paused": engine.paused,
                "killed": engine.killed,
            },
        )

    def _can_send(self) -> str | None:
        if not self.fence.alive:
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
        if self.pipeline is not None and not self.pipeline.healthy:
            return "pipeline_unhealthy"
        if (cannot := self._can_send()) is not None:
            return cannot
        # Последней: под блоком трат цикл ещё может продолжить забег метро.
        if gateway is not None and gateway.spending_blocked is not None:
            return "spending_blocked"
        return None

    async def _make_transport(self, pipeline: Pipeline) -> tuple[Transport, TgAuthBackend]:
        config = self._deps.config
        if config.transport == "fake":
            return FakeTransport(), FakeTgBackend(authorized=False)
        box = self._deps.box
        assert box is not None
        had_account_app = await self._deps.accounts.tg_app(self.account_id) is not None
        api_id, api_hash = await load_tg_app(
            self._deps.accounts, box, config, self.account_id, self.notifier
        )
        storage = await self._session_storage()
        if (
            had_account_app
            and await storage.user_id() is not None
            and await storage.api_id() != api_id
        ):
            await storage.api_id(api_id)
        kurigram = KurigramTransport(
            api_id=api_id,
            api_hash=api_hash,
            account_id=self.account_id,
            storage=storage,
            fence=self.fence,
            chat_filter=ChatFilter.from_settings(self.settings.current.chats),
            sink=pipeline.submit,
            backlog=pipeline.backlog,
        )
        self._kurigram = kurigram
        return kurigram, kurigram

    async def reload_tg_app(self) -> None:
        with _in_account(self.account_id):
            kurigram = self._kurigram
            if kurigram is not None:
                box = self._deps.box
                assert box is not None
                api_id, api_hash = await load_tg_app(
                    self._deps.accounts, box, self._deps.config, self.account_id, self.notifier
                )
                await kurigram.set_app(api_id, api_hash)
            if self.tg is not None:
                await self.tg.drop_attempt()

    async def _session_storage(self) -> PgSessionStorage:
        """Сессия Telegram аккаунта в базе (раздел 4.3 спеки); пиры, которые она держит в базе, —
        чаты из текущих настроек и пользователь swinfo. Сессия, которая не расшифровалась,
        удаляется: движок стартует без Telegram, нужен вход заново. Аккаунт 1 без вошедшей
        сессии перенимает файл сессии прежней установки (раздел 4.7)."""
        from app.db.tg_storage import PgSessionStorage

        box = self._deps.box
        # Без ключа процесс с kurigram не стартует (`Runtime._check_key`).
        assert box is not None
        settings = self.settings
        storage = PgSessionStorage(
            self._deps.db, self.account_id, box, lambda: session_peers(settings.current.chats)
        )
        try:
            await storage.open()
        except Undecryptable:
            log.error("telegram session not decrypted, deleted")
            await storage.delete()
            await self.notifier.notify(
                "error",
                "tg_session_unreadable",
                "telegram session could not be decrypted and was deleted; login again in admin",
            )
        # Строки нет или она недописана прерванным переносом (`user_id` пишется последним).
        if self.account_id == 1 and await storage.user_id() is None:
            await self._import_session_file(storage)
        return storage

    async def _import_session_file(self, storage: PgSessionStorage) -> None:
        """Перенос `<data_dir>/pyrobot.session` в базу; файла нет — ничего. Сбой —
        предупреждение аккаунта, файл остаётся, аккаунт требует входа."""
        from app.db.tg_storage import import_session_file

        path = self._deps.config.data_dir / LEGACY_SESSION_FILE
        peers = session_peers(self.settings.current.chats)
        try:
            imported = await import_session_file(path, storage, peers)
        except Exception as exc:
            log.warning("telegram session file %s not imported", path, exc_info=True)
            await self.notifier.notify(
                "warn",
                "tg_session_import_failed",
                f"{path.name} not imported ({type(exc).__name__}); login again in admin",
            )
            return
        if imported:
            log.info("telegram session imported from %s", path)

    async def _probe_tg(self) -> None:
        while True:
            await asyncio.sleep(self.tg_probe_s)
            kurigram, tg = self._kurigram, self.tg
            if kurigram is not None and tg is not None and tg.status().state is TgState.ONLINE:
                await kurigram.probe()
