import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Строка аккаунта под FOR SHARE до коммита: захват аренды (UPDATE accounts) другим хостом ждёт
# ограждённую транзакцию и видит новую эпоху только после неё.
_GUARD = text("SELECT 1 FROM accounts WHERE id = :id AND lease_epoch = :epoch FOR SHARE")


class LeaseLost(Exception):
    """Аренда аккаунта потеряна или наступил её местный срок: вызовы Telegram и записи
    аккаунта запрещены."""


class Fence:
    """Ограда аренды аккаунта: вызовы Telegram и пишущие транзакции движка идут через неё и не
    выходят за местный срок `deadline` (по часам `monotonic`). Наступивший срок и отзыв
    конечны: продление ограду уже не оживляет."""

    def __init__(
        self,
        account_id: int,
        epoch: int,
        deadline: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.account_id = account_id
        self.epoch = epoch
        self.deadline = deadline
        # Вызывается один раз — при первом обнаружении потери (отзыв или наступивший срок).
        self.on_lost: Callable[[], None] | None = None
        self._monotonic = monotonic
        self._revoked = False
        # Сроки идущих вызовов в часах цикла: продление сдвигает их вместе с `deadline`.
        self._timeouts: set[asyncio.Timeout] = set()

    @property
    def alive(self) -> bool:
        return not self._revoked and self._monotonic() < self.deadline

    def extend(self, epoch: int, deadline: float) -> None:
        if epoch != self.epoch or not self.alive:
            return
        shift = deadline - self.deadline
        self.deadline = deadline
        for timeout in self._timeouts:
            when = timeout.when()
            # Сработавший срок вызова уже не сдвинуть: вызов обрывается.
            if when is not None and not timeout.expired():
                timeout.reschedule(when + shift)

    def revoke(self) -> None:
        if self._revoked:
            return
        self._revoked = True
        if self.on_lost is not None:
            try:
                self.on_lost()
            except Exception:
                log.exception("lease lost handler failed: account %s", self.account_id)

    def check(self) -> None:
        if not self.alive:
            self.revoke()
            raise LeaseLost(f"account {self.account_id} lease (epoch {self.epoch}) lost")

    async def call[T](self, fn: Callable[[], Awaitable[T]]) -> T:
        """`fn()` не начинается после срока и обрывается на нём: `invoke` kurigram сам может
        ждать запуска сессии до 15 с. Свой `TimeoutError` вызова пробрасывается как есть."""
        self.check()
        loop = asyncio.get_running_loop()
        timeout = asyncio.timeout_at(loop.time() + self.deadline - self._monotonic())
        self._timeouts.add(timeout)
        try:
            async with timeout:
                return await fn()
        except TimeoutError:
            if not timeout.expired():
                raise
            self.revoke()
            raise LeaseLost(
                f"account {self.account_id} lease (epoch {self.epoch}) expired during call"
            ) from None
        finally:
            self._timeouts.discard(timeout)

    async def guard(self, session: AsyncSession) -> None:
        """Первый запрос пишущей транзакции аккаунта: эпоха в базе всё ещё своя."""
        self.check()
        row = await session.scalar(_GUARD, {"id": self.account_id, "epoch": self.epoch})
        if row is None:
            self.revoke()
            raise LeaseLost(f"account {self.account_id} lease epoch {self.epoch} is stale")
