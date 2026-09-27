from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.engine.gateway.gateway import ActionGateway
from app.engine.gateway.types import ActionRequest, ActionResult
from app.engine.lag import LoopLagMonitor
from app.engine.manual import Fingerprint, KeyReused, fingerprint, manual_key
from app.engine.notify import NotifierPort
from app.engine.pipeline import Pipeline
from app.engine.settings import (
    Settings,
    SettingsPatchError,
    SettingsProvider,
    apply_patch,
    settings_diff,
)
from app.engine.tg_auth import TgAuthManager, TgState, TgStatus

if TYPE_CHECKING:
    from app.engine.planner.loop import PlannerLoop
    from app.engine.reconcile import Reconciler
    from app.engine.stream import EventStream

log = logging.getLogger(__name__)


def _always() -> bool:
    return True


class LockLostError(Exception):
    pass


class PlannerUnavailable(Exception):
    pass


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
    lock_ok: bool
    loop_lag_ms: float


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
        lag: LoopLagMonitor,
        lock_ok: Callable[[], bool] = _always,
        workers_ok: Callable[[], bool] = _always,
        notifier: NotifierPort | None = None,
        reconciler: Reconciler | None = None,
        planner: PlannerLoop | None = None,
        stream: EventStream | None = None,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.pipeline = pipeline
        self.tg = tg_auth
        self.lag = lag
        self._lock_ok = lock_ok
        self._workers_ok = workers_ok
        self._notifier = notifier
        self._reconciler = reconciler
        self._planner = planner
        self._manual: set[asyncio.Future[ActionResult]] = set()
        # Отпечатки ручных действий в полёте: повтор ключа с другими параметрами отклоняется
        # и до записи ключа в БД.
        self._inflight: dict[str, Fingerprint] = {}
        self.stream = stream

    def state(self) -> tuple[int, dict[str, Any]]:
        return self.pipeline.version, self.pipeline.state

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
            lock_ok=self._lock_ok(),
            loop_lag_ms=self.lag.lag_ms,
        )

    def ready(self) -> bool:
        st = self.status()
        return (
            st.lock_ok
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
        # Без блокировки единственного экземпляра latch не снимается: иначе на
        # одном аккаунте могут оказаться два отправителя.
        if not self._lock_ok():
            raise LockLostError("single-instance lock lost")

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
        if self._planner is None:
            raise PlannerUnavailable
        return await self._planner.request(name, params, key=key, by=by)

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
        Переход в `live` — только с `confirm_live`: из dry_run начинаются реальные траты."""
        before: list[Settings] = []

        def change(s: Settings) -> Settings:
            new = apply_patch(s, changes)
            if new.engine.mode == "live" and s.engine.mode != "live" and not confirm_live:
                raise SettingsPatchError("live_requires_confirm", "engine.mode")
            before.append(s)
            return new

        new, saved = await self.settings.update(change, changed_by=by, expected_version=version)
        old = before[-1]
        await self.gateway.wake()
        if self._planner is not None:
            self._planner.wake()
        if old.engine.mode != new.engine.mode:
            await self._audit(
                "engine_mode", f"mode {old.engine.mode} -> {new.engine.mode} by {by}"
            )
        changed = settings_diff(old.model_dump(mode="json"), new.model_dump(mode="json"))
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
