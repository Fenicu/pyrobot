from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.engine.gateway.gateway import ActionGateway
from app.engine.lag import LoopLagMonitor
from app.engine.notify import NotifierPort
from app.engine.pipeline import Pipeline
from app.engine.settings import Settings, SettingsProvider
from app.engine.tg_auth import TgAuthManager, TgState, TgStatus

log = logging.getLogger(__name__)


def _always() -> bool:
    return True


class LockLostError(Exception):
    pass


@dataclass(frozen=True)
class EngineStatus:
    mode: str
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
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.pipeline = pipeline
        self.tg = tg_auth
        self.lag = lag
        self._lock_ok = lock_ok
        self._workers_ok = workers_ok
        self._notifier = notifier

    def status(self) -> EngineStatus:
        eng = self.settings.current.engine
        inflight = self.gateway.in_flight
        label = (inflight.text or inflight.data) if inflight else None
        latch = self.gateway.kill_reason
        kill_reason = latch if latch is not None else (eng.kill_reason if eng.killed else None)
        return EngineStatus(
            mode=eng.mode,
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

    async def reconciled(self, *, by: str) -> None:
        log.info("spending unblocked by %s", by)
        await self.gateway.allow_spending()
        await self._audit("engine_reconciled", f"spending unblocked by {by}")

    async def _audit(self, code: str, text: str) -> None:
        if self._notifier is not None:
            await self._notifier.notify("info", code, text)
