import asyncio
import hashlib
import secrets
import time
from collections.abc import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Длина пароля админа: нижняя граница — для нового пароля (API и CLI), верхняя — чтобы им можно
# было войти (поле входа ограничено тем же).
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024

_hasher = PasswordHasher()
_dummy_hash: str | None = None
_dummy_hash_lock = asyncio.Lock()


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    try:
        return await asyncio.to_thread(_hasher.verify, password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


async def dummy_hash() -> str:
    """Хэш для неизвестного логина — уравнивает время verify_password с известным."""
    global _dummy_hash
    if _dummy_hash is None:
        async with _dummy_hash_lock:
            if _dummy_hash is None:
                _dummy_hash = await hash_password(secrets.token_urlsafe(32))
    return _dummy_hash


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginRateLimiter:
    def __init__(
        self,
        free_attempts: int = 5,
        base_s: float = 30.0,
        max_s: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
        verify_slots: int = 2,
        window_s: float = 3600.0,
        max_entries: int = 10_000,
    ) -> None:
        self._free = free_attempts
        self._base = base_s
        self._max = max_s
        self._clock = clock
        self._window = window_s
        self._max_entries = max_entries
        self._failures: dict[str, tuple[int, float]] = {}
        self._until: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.slots = asyncio.Semaphore(verify_slots)

    @property
    def tracked(self) -> int:
        return len(self._failures.keys() | self._locks.keys())

    def lock_for(self, key: str) -> asyncio.Lock:
        if len(self._locks) > self._max_entries:
            self._sweep()
        return self._locks.setdefault(key, asyncio.Lock())

    def blocked_for(self, key: str) -> float:
        now = self._clock()
        if self._stale(key, now):
            self._forget(key)
        return max(0.0, self._until.get(key, 0.0) - now)

    def failure(self, key: str) -> None:
        now = self._clock()
        if self._stale(key, now):
            self._forget(key)
        count = self._failures.get(key, (0, now))[0] + 1
        self._failures[key] = (count, now)
        if count > self._free:
            delay = min(self._max, self._base * 2 ** (count - self._free - 1))
            self._until[key] = now + delay
        if len(self._failures) > self._max_entries:
            self._sweep()

    def success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._until.pop(key, None)

    def _stale(self, key: str, now: float) -> bool:
        entry = self._failures.get(key)
        if entry is None:
            return False
        return now - entry[1] > self._window and self._until.get(key, 0.0) <= now

    def _forget(self, key: str) -> None:
        self._failures.pop(key, None)
        self._until.pop(key, None)

    def _sweep(self) -> None:
        now = self._clock()
        for key in [k for k in self._failures if self._stale(k, now)]:
            self._forget(key)
        # release() снимает _locked до того, как разбуженный waiter уходит из _waiters;
        # удалить лок в этом окне значит завести новый Lock под тем же ключом и потерять
        # сериализацию с уже ожидающим вызовом.
        idle = [
            k
            for k, lock in self._locks.items()
            if not lock.locked() and not getattr(lock, "_waiters", None)
        ]
        for key in idle:
            if key not in self._failures:
                del self._locks[key]
