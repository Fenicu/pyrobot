"""Лимит запросов кода входа в Telegram (раздел 4.3 спеки): один на процесс."""

import time
from collections import deque
from collections.abc import Callable


class CodeLimiter:
    """Не больше `per_host` запросов кода на процесс и `per_account` на аккаунт за скользящее
    окно `window_s`. Отказ квоту не тратит."""

    def __init__(
        self,
        per_host: int,
        per_account: int = 3,
        *,
        window_s: float = 3600.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._per_host = per_host
        self._per_account = per_account
        self._window = window_s
        self._monotonic = monotonic
        self._host: deque[float] = deque()
        self._accounts: dict[int, deque[float]] = {}

    def take(self, account_id: int) -> float | None:
        """Запрос кода аккаунтом `account_id`: None — разрешён и учтён, иначе через сколько
        секунд освободится место."""
        now = self._monotonic()
        host = self._expire(self._host, now)
        account = self._expire(self._accounts.get(account_id, deque()), now)
        waits = [
            (taken[0] if taken else now) + self._window - now
            for taken, limit in ((host, self._per_host), (account, self._per_account))
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
