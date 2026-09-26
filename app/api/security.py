import asyncio
import hashlib
import secrets
import time
from collections.abc import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    try:
        return await asyncio.to_thread(_hasher.verify, password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


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
    ) -> None:
        self._free = free_attempts
        self._base = base_s
        self._max = max_s
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._until: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.slots = asyncio.Semaphore(verify_slots)

    def lock_for(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def blocked_for(self, key: str) -> float:
        return max(0.0, self._until.get(key, 0.0) - self._clock())

    def failure(self, key: str) -> None:
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count > self._free:
            delay = min(self._max, self._base * 2 ** (count - self._free - 1))
            self._until[key] = self._clock() + delay

    def success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._until.pop(key, None)
