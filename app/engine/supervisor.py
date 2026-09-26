from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from app.engine.notify import NotifierPort

log = logging.getLogger(__name__)
Factory = Callable[[], Awaitable[None]]


class Supervisor:
    def __init__(
        self, notifier: NotifierPort, *, base_s: float = 1.0, max_s: float = 60.0
    ) -> None:
        self._notifier = notifier
        self._base = base_s
        self._max = max_s
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._backoff: set[str] = set()

    def start(self, name: str, factory: Factory) -> None:
        self._tasks[name] = asyncio.create_task(self._keep(name, factory), name=name)

    def healthy(self) -> bool:
        return (
            bool(self._tasks)
            and not self._backoff
            and all(not t.done() for t in self._tasks.values())
        )

    async def cancel(self, name: str) -> None:
        """Отменить одну задачу и дождаться её; неизвестное имя — ничего."""
        task = self._tasks.pop(name, None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._backoff.discard(name)

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    async def _keep(self, name: str, factory: Factory) -> None:
        delay = self._base
        while True:
            try:
                await factory()
                log.warning("task %s exited, restarting", name)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("task %s crashed", name)
                await self._notifier.notify("error", f"task_failed:{name}", f"{name} crashed")
            self._backoff.add(name)
            await asyncio.sleep(delay)
            self._backoff.discard(name)
            delay = min(delay * 2, self._max)
