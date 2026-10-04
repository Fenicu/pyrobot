"""Учёт открытых потоков SSE на учётку (раздел 5.3 спеки)."""

from collections import defaultdict
from collections.abc import Callable


class SseSlot:
    """Занятый слот SSE-потока. Освобождение идемпотентно."""

    def __init__(self, on_release: Callable[[], None]) -> None:
        self._on_release = on_release
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._on_release()


class SseSlots:
    """Счётчик активных потоков SSE по пользователям."""

    def __init__(self) -> None:
        self._counts: dict[int, int] = defaultdict(int)

    def count(self, user_id: int) -> int:
        return self._counts.get(user_id, 0)

    def take(self, user_id: int, limit: int) -> SseSlot | None:
        if self._counts[user_id] >= limit:
            return None
        self._counts[user_id] += 1

        def _release() -> None:
            self._counts[user_id] -= 1
            if self._counts[user_id] <= 0:
                self._counts.pop(user_id, None)

        return SseSlot(_release)
