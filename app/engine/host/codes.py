"""Лимит запросов кода входа в Telegram (раздел 4.3 спеки): один на процесс."""

import time
from collections import deque
from collections.abc import Callable


class CodeLimiter:
    """Не больше `per_host` запросов кода на процесс и `per_account` на аккаунт за скользящее
    окно `window_s`. Отказ квоту не тратит."""

    def __init__(
        self,
        limits: int | Callable[[], tuple[int, int]],
        per_account: int = 3,
        *,
        on_host_limit: Callable[[], None] | None = None,
        window_s: float = 3600.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = limits
        self._per_account = per_account
        self._on_host_limit = on_host_limit
        self._window = window_s
        self._monotonic = monotonic
        self._host: deque[float] = deque()
        self._accounts: dict[int, deque[float]] = {}
        self._last_host_limit_notified: float = float("-inf")

    def take(self, account_id: int) -> float | None:
        """Запрос кода аккаунтом `account_id`: None — разрешён и учтён, иначе через сколько
        секунд освободится место."""
        now = self._monotonic()
        host = self._expire(self._host, now)
        account = self._expire(self._accounts.get(account_id, deque()), now)
        if callable(self._limits):
            per_host, per_account = self._limits()
        else:
            per_host, per_account = self._limits, self._per_account

        if len(host) >= per_host and self._on_host_limit is not None:
            if now - self._last_host_limit_notified >= self._window:
                self._last_host_limit_notified = now
                self._on_host_limit()

        waits = [
            (taken[0] if taken else now) + self._window - now
            for taken, limit in ((host, per_host), (account, per_account))
            if len(taken) >= limit
        ]
        if waits:
            return max(waits)
        host.append(now)
        account.append(now)
        self._accounts[account_id] = account
        return None

    def _expire(self, taken: deque[float], now: float) -> deque[float]:
        while taken and taken[0] <= now - self._window:
            taken.popleft()
        return taken
