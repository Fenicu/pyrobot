from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from app.engine.artifact import ArtifactConflict, ArtifactRuns
from app.engine.clock import SystemClock
from app.engine.gadgets import GadgetConflict, GadgetRuns
from app.engine.gateway.gateway import ActionGateway
from app.engine.gateway.types import ActionRequest, ActionResult
from app.engine.manual import Fingerprint, KeyReused, fingerprint, manual_key
from app.engine.notify import LogNotifier, NotifierPort
from app.engine.pipeline import Pipeline
from app.engine.planner.decide import Outlook
from app.engine.planner.loop import PlanView
from app.engine.scenarios.registry import SCENARIOS
from app.engine.settings import (
    ArtifactKey,
    Settings,
    SettingsPatch,
    SettingsProvider,
    UpgradeChoice,
    UpSlotKey,
    settings_diff,
)
from app.engine.state.model import company_of, load_state
from app.engine.tg_auth import TgAuthManager, TgState, TgStatus
from app.engine.transport.base import GAME_CHAT_USERNAME, JoinStatus, Transport

if TYPE_CHECKING:
    from app.engine.planner.loop import PlannerLoop
    from app.engine.reconcile import Reconciler
    from app.engine.server_settings import EngineBounds
    from app.engine.stream import EventStream

log = logging.getLogger(__name__)
# «План бота» не пересчитывается чаще: несколько вкладок не гоняют проход планировщика.
OUTLOOK_TTL_S = 5.0


def _always() -> bool:
    return True


class LockLostError(Exception):
    pass


class PlannerUnavailable(Exception):
    pass


class TgNotOnline(Exception):
    pass


class ScenarioNotManual(Exception):
    """Сценарий запускает только планировщик (`ScenarioSpec.manual`)."""


def _no_watch() -> GameChatWatch | None:
    return None


class GameChatWatch(Protocol):
    """Членство аккаунта в общем чате игры по сверке истории (`HistorySync`)."""

    @property
    def game_chat_member(self) -> bool | None: ...

    def game_chat_joined(self) -> None: ...


@dataclass(frozen=True)
class EngineStatus:
    mode: str
    paused: bool
    scenario: str | None
    next_wake: datetime | None
    killed: bool
    kill_reason: str | None
    spending_blocked: str | None
    tg: TgStatus
    queue: int
    in_flight: str | None
    pipeline_backlog: int
    pipeline_healthy: bool
    workers_ok: bool
    # Аренда аккаунта действует (ограда жива).
    lease_ok: bool
    # Аккаунт состоит в общем чате игры (по сверке истории); None — ещё не проверено.
    game_chat_member: bool | None = None


@dataclass(frozen=True)
class SettingsUpdate:
    settings: Settings
    version: int
    changed: dict[str, list[Any]]


class EngineFacade:
    def __init__(
        self,
        *,
        settings: SettingsProvider,
        gateway: ActionGateway,
        pipeline: Pipeline,
        tg_auth: TgAuthManager,
        lease_ok: Callable[[], bool] = _always,
        workers_ok: Callable[[], bool] = _always,
        notifier: NotifierPort | None = None,
        reconciler: Reconciler | None = None,
        planner: PlannerLoop | None = None,
        stream: EventStream | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        transport: Transport | None = None,
        history: Callable[[], GameChatWatch | None] = _no_watch,
        artifacts: ArtifactRuns | None = None,
        gadgets: GadgetRuns | None = None,
        bounds: Callable[[], EngineBounds] | None = None,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.pipeline = pipeline
        self.tg = tg_auth
        self._lease_ok = lease_ok
        self._workers_ok = workers_ok
        self._notifier = notifier
        self._reconciler = reconciler
        self._planner = planner
        self._manual: set[asyncio.Future[ActionResult]] = set()
        # Отпечатки ручных действий в полёте: повтор ключа с другими параметрами отклоняется
        # и до записи ключа в БД.
        self._inflight: dict[str, Fingerprint] = {}
        self.stream = stream
        self._monotonic = monotonic
        self._transport = transport
        # Сверка истории стартует после фасада (от выхода в онлайн) — поэтому функция.
        self._history = history
        self._bounds = bounds
        self._outlook: tuple[tuple[int, int, int], float, datetime, Outlook] | None = None
        self.artifacts = artifacts or ArtifactRuns(
            settings=settings,
            state=lambda: load_state(pipeline.state),
            notifier=notifier or LogNotifier(),
            clock=SystemClock(),
        )
        self.gadgets = gadgets or GadgetRuns(
            settings=settings,
            state=lambda: load_state(pipeline.state),
            notifier=notifier or LogNotifier(),
            clock=SystemClock(),
        )

    def state(self) -> tuple[int, dict[str, Any]]:
        return self.pipeline.version, self.pipeline.state

    def own_company(self) -> str | None:
        return company_of(self.pipeline.state)

    def status(self) -> EngineStatus:
        eng = self.settings.current.engine
        inflight = self.gateway.in_flight
        label = (inflight.text or inflight.data) if inflight else None
        latch = self.gateway.kill_reason
        kill_reason = latch if latch is not None else (eng.kill_reason if eng.killed else None)
        planner = self._planner
        return EngineStatus(
            mode=eng.mode,
            paused=eng.paused,
            scenario=planner.current if planner is not None else None,
            next_wake=planner.next_wake if planner is not None else None,
            killed=latch is not None or eng.killed,
            kill_reason=kill_reason,
            spending_blocked=self.gateway.spending_blocked,
            tg=self.tg.status(),
            queue=self.gateway.queue_size,
            in_flight=label,
            pipeline_backlog=self.pipeline.backlog(),
            pipeline_healthy=self.pipeline.healthy,
            workers_ok=self._workers_ok(),
            lease_ok=self._lease_ok(),
            game_chat_member=self.game_chat_member(),
        )

    def game_chat_member(self) -> bool | None:
        watch = self._history()
        return watch.game_chat_member if watch is not None else None

    async def join_game_chat(self) -> JoinStatus:
        """Вступление в общий чат игры — вне шлюза команд, как выход из Telegram. Вступил или уже
        участник — сверка сразу перечитывает чат."""
        if self._transport is None or self.tg.status().state is not TgState.ONLINE:
            raise TgNotOnline
        chat_id = self.settings.current.chats.swinfo_chat_id
        status = await self._transport.join_chat(GAME_CHAT_USERNAME, chat_id)
        watch = self._history()
        if status != "request_sent" and watch is not None:
            watch.game_chat_joined()
        return status

    async def send_saved(self, text: str) -> None:
        """Отправка сообщения в «Избранное» (Saved Messages) текущего аккаунта."""
        if self._transport is None or self.tg.status().state is not TgState.ONLINE:
            raise TgNotOnline
        await self._transport.send_saved(text)

    def ready(self) -> bool:
        st = self.status()
        return (
            st.lease_ok
            and st.workers_ok
            and st.tg.state is TgState.ONLINE
            and not st.killed
            and st.spending_blocked is None
            and st.pipeline_healthy
        )

    async def kill(self, reason: str, *, by: str) -> None:
        await self.gateway.kill(reason)

        def change(s: Settings) -> Settings:
            engine = s.engine.model_copy(update={"killed": True, "kill_reason": reason})
            return s.model_copy(update={"engine": engine})

        try:
            await self.settings.update(change, changed_by=by)
        except Exception:
            log.exception("kill switch not persisted; latch stays active")
        await self._audit("engine_killed", f"kill switch on by {by}: {reason}")

    async def unkill(self, *, by: str) -> None:
        # Без аренды аккаунта latch не снимается: иначе на одном аккаунте могут оказаться два
        # отправителя.
        if not self._lease_ok():
            raise LockLostError("account lease lost")

        def change(s: Settings) -> Settings:
            engine = s.engine.model_copy(update={"killed": False, "kill_reason": None})
            return s.model_copy(update={"engine": engine})

        await self.settings.update(change, changed_by=by)
        await self.gateway.unkill()
        await self._audit("engine_unkilled", f"kill switch off by {by}")

    async def pause(self, *, by: str) -> None:
        await self._set_paused(True, by)
        await self._audit("engine_paused", f"planner paused by {by}")

    async def resume(self, *, by: str) -> None:
        await self._set_paused(False, by)
        await self._audit("engine_resumed", f"planner resumed by {by}")

    async def _set_paused(self, paused: bool, by: str) -> None:
        def change(s: Settings) -> Settings:
            engine = s.engine.model_copy(update={"paused": paused})
            return s.model_copy(update={"engine": engine})

        await self.settings.update(change, changed_by=by)
        self._wake_planner()

    async def artifact_start(self, artifact: ArtifactKey, *, lottery_max: bool, by: str) -> None:
        """Запрос на сбор: только с Telegram в сети и в live, иначе запуск не уйдёт в игру."""
        if self.tg.status().state is not TgState.ONLINE:
            raise ArtifactConflict("tg_not_online")
        if self.settings.current.engine.mode != "live":
            raise ArtifactConflict("dry_run")
        await self.artifacts.start(artifact, lottery_max=lottery_max, by=by)
        self._wake_planner()

    async def artifact_pause(self, *, by: str) -> None:
        await self.artifacts.pause(by=by)
        self._wake_planner()

    async def artifact_resume(self, *, by: str) -> None:
        await self.artifacts.resume(by=by)
        self._wake_planner()

    async def artifact_cancel(self, *, by: str) -> None:
        await self.artifacts.cancel(by=by)
        self._wake_planner()

    async def artifact_adopt(self, *, by: str) -> None:
        await self.artifacts.adopt(by=by)
        self._wake_planner()

    async def gadget_upgrade_start(
        self, slot: UpSlotKey, target: int, kind: UpgradeChoice, *, by: str
    ) -> None:
        """Задача заточки: только с Telegram в сети и в live, иначе клики не уйдут в игру."""
        if self.tg.status().state is not TgState.ONLINE:
            raise GadgetConflict("tg_not_online")
        if self.settings.current.engine.mode != "live":
            raise GadgetConflict("dry_run")
        await self.gadgets.start(slot, target, kind, by=by)
        self._wake_planner()

    async def gadget_upgrade_stop(self, *, by: str) -> None:
        await self.gadgets.stop(by=by)
        self._wake_planner()

    def _wake_planner(self) -> None:
        if self._planner is not None:
            self._planner.wake()

    async def manual(self, req: ActionRequest, *, wait_s: float) -> ActionResult | None:
        """Ручное действие через шлюз. None — не завершилось за `wait_s`: оно продолжает
        исполняться, итог отдаст повтор с тем же ключом идемпотентности. KeyReused — ключ
        занят действием в полёте с другими параметрами."""
        key = req.idempotency_key
        if key is not None:
            known = self._inflight.get(key)
            if known is not None and known != fingerprint(req):
                raise KeyReused(key)
        task = asyncio.ensure_future(self.gateway.submit(req))
        self._manual.add(task)
        task.add_done_callback(self._manual.discard)
        if key is not None and key not in self._inflight:
            self._inflight[key] = fingerprint(req)
            task.add_done_callback(lambda _: self._inflight.pop(key, None))
        try:
            return await asyncio.wait_for(asyncio.shield(task), wait_s)
        except TimeoutError:
            return None

    async def run_scenario(
        self, name: str, params: Mapping[str, Any], *, key: str, by: str
    ) -> tuple[int, bool]:
        """Ручной запуск сценария через очередь планировщика; KeyError — нет такого сценария."""
        spec = SCENARIOS.get(name)
        if spec is not None and not spec.manual:
            raise ScenarioNotManual(name)
        if self._planner is None:
            raise PlannerUnavailable
        return await self._planner.request(name, params, key=key, by=by)

    async def outlook(self) -> PlanView:
        """«План бота». Проход планировщика живёт до `OUTLOOK_TTL_S` при той же версии
        состояния, версии настроек и отметке цикла; состояние цикла (пауза, готовность, очередь)
        — всегда свежее. PlannerUnavailable — задача цикла не идёт (ещё не запущена или
        перезапускается после падения)."""
        planner = self._planner
        if planner is None or not planner.running:
            raise PlannerUnavailable
        key = (self.pipeline.version, self.settings.version, planner.revision)
        at = self._monotonic()
        cached = self._outlook
        if cached is not None and cached[0] == key and at - cached[1] < OUTLOOK_TTL_S:
            now, view = cached[2], cached[3]
        else:
            now, view = await planner.plan()
            self._outlook = (key, at, now, view)
        return PlanView(now, view, planner.loop_view())

    def manual_pending(self, key: str) -> bool:
        return self.gateway.pending_key(manual_key(key))

    async def patch_settings(
        self,
        changes: Mapping[str, Any],
        *,
        version: int,
        by: str,
        confirm_live: bool = False,
    ) -> SettingsUpdate:
        """Частичное изменение настроек с оптимистичной блокировкой по `version`.
        Переход в `live` — только с `confirm_live`: из dry_run начинаются реальные траты.
        Поле `chats.*`, равное пользователю Telegram привязанного аккаунта, — `ChatIsSelf`."""
        bounds = self._bounds() if self._bounds is not None else None
        patch = SettingsPatch(
            changes,
            confirm_live=confirm_live,
            self_id=self.tg.status().bound_user_id,
            bounds=bounds,
        )
        new, saved = await self.settings.update(patch, changed_by=by, expected_version=version)
        old = patch.before
        assert old is not None
        await self.gateway.wake()
        if self._planner is not None:
            self._planner.wake()
        old_mode = old["engine"]["mode"]
        if old_mode != new.engine.mode:
            await self._audit("engine_mode", f"mode {old_mode} -> {new.engine.mode} by {by}")
        changed = settings_diff(old, new.model_dump(mode="json"))
        return SettingsUpdate(new, saved, changed)

    async def reconciled(self, *, by: str) -> None:
        log.info("spending unblocked by %s", by)
        if self._reconciler is not None:
            await self._reconciler.override()
        else:
            await self.gateway.allow_spending()
        await self._audit("engine_reconciled", f"spending unblocked by {by}")

    async def _audit(self, code: str, text: str) -> None:
        if self._notifier is not None:
            await self._notifier.notify("info", code, text)
