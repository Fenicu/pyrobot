from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable

from app.engine.notify import NotifierPort

log = logging.getLogger(__name__)
Factory = Callable[[], Awaitable[None]]


class Supervisor:
    """Перезапуск задач с нарастающей паузой. Сбой одной задачи `crash_limit` раз за
    `crash_window_s` — серия сбоев: задача больше не перезапускается, один вызов
    `on_crash_loop(имя)`."""

    def __init__(
        self,
        notifier: NotifierPort,
        *,
        base_s: float = 1.0,
        max_s: float = 60.0,
        crash_limit: int = 5,
        crash_window_s: float = 600.0,
        on_crash_loop: Callable[[str], Awaitable[None]] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._notifier = notifier
        self._base = base_s
        self._max = max_s
        self._crash_limit = crash_limit
        self._crash_window = crash_window_s
        self._on_crash_loop = on_crash_loop
        self._monotonic = monotonic
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
        await _finish([task])
        self._backoff.discard(name)

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await _finish(tasks)
        self._tasks.clear()

    async def _keep(self, name: str, factory: Factory) -> None:
        delay = self._base
        crashes: deque[float] = deque()
        while True:
            try:
                await factory()
                log.warning("task %s exited, restarting", name)
            except asyncio.CancelledError:
                raise
            except Exception:
                if _cancelling():
                    # Отменённая задача ответила на отмену ошибкой (запись в finally после
                    # потери аренды — LeaseLost): отмена в силе, перезапуска нет.
                    log.warning("task %s failed while cancelled", name, exc_info=True)
                    raise asyncio.CancelledError from None
                log.exception("task %s crashed", name)
                await self._notify_crash(name)
                now = self._monotonic()
                crashes.append(now)
                while crashes and crashes[0] <= now - self._crash_window:
                    crashes.popleft()
                if len(crashes) >= self._crash_limit:
                    log.error(
                        "task %s crashed %d times in %.0fs, not restarted",
                        name,
                        len(crashes),
                        self._crash_window,
                    )
                    await self._crash_loop(name)
                    return
            self._backoff.add(name)
            await asyncio.sleep(delay)
            self._backoff.discard(name)
            delay = min(delay * 2, self._max)

    async def _notify_crash(self, name: str) -> None:
        try:
            await self._notifier.notify("error", f"task_failed:{name}", f"{name} crashed")
        except Exception:
            # После потери аренды уведомление аккаунта не пишется (LeaseLost): сбой уже в логе.
            log.warning("crash of task %s not notified", name, exc_info=True)

    async def _crash_loop(self, name: str) -> None:
        if self._on_crash_loop is None:
            return
        try:
            await self._on_crash_loop(name)
        except Exception:
            log.exception("crash loop of task %s not handled", name)


def _cancelling() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


async def _finish(tasks: Iterable[asyncio.Task[None]]) -> None:
    """Дождаться завершения задач: исход каждой забирается, ошибка — в лог, а не наружу, и
    остальные задачи дожидаются. Отмена самого ожидания выходит наружу."""
    pending = set(tasks)
    if not pending:
        return
    await asyncio.wait(pending)
    for task in pending:
        if not task.cancelled() and (exc := task.exception()) is not None:
            log.error("task %s failed on stop", task.get_name(), exc_info=exc)
