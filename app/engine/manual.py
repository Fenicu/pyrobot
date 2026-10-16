"""Ручные команды из админки: запросы шлюзу от имени MANUAL и их ожидание ответа игры."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.engine.bus import Delivery
from app.engine.clock import Clock
from app.engine.commands import CommandClass
from app.engine.gateway.types import (
    ActionKind,
    ActionRequest,
    Expectation,
    Match,
    Source,
    Verdict,
)
from app.engine.parsing.refusals import Busy, Refused

# Ручное управление: столько после взятия и после каждой ручной команды.
MANUAL_CONTROL_SPAN = timedelta(minutes=30)

# Свои ключи идемпотентности ручных команд не пересекаются с ключами движка.
KEY_PREFIX = "manual:"


def manual_key(key: str) -> str:
    return KEY_PREFIX + key


Fingerprint = tuple[str, int, dict[str, Any]]


def fingerprint(req: ActionRequest) -> Fingerprint:
    """Что именно просили сделать: повтор ключа с другим отпечатком — ошибка клиента."""
    return req.kind.value, req.chat_id, req.payload()


class KeyReused(Exception):
    """Ключ идемпотентности уже занят запросом с другими параметрами."""


def any_reply(delivery: Delivery) -> Match | None:
    """Смысл ручной команды движку неизвестен: подтверждает первый ответ игры в чате,
    отказ игры (занят, не хватает ресурсов) — отказ."""
    for event in delivery.events:
        if isinstance(event, Refused | Busy):
            return Match(Verdict.REFUSED, getattr(event, "reason", None) or event.kind)
    return Match(Verdict.CONFIRMED, delivery.events[0].kind if delivery.events else "reply")


def _expect(cls: CommandClass) -> Expectation | None:
    # nav подтверждается отправкой; остальному шлюз требует ожидание ответа.
    return None if cls is CommandClass.NAV else Expectation(any_reply)


# Подтверждение risky: версия состояния и срок токена (шлюз сверяет их перед отправкой).
Confirm = tuple[int, datetime]


def send_request(
    text: str, *, chat_id: int, cls: CommandClass, key: str | None, confirm: Confirm | None
) -> ActionRequest:
    return ActionRequest(
        kind=ActionKind.SEND,
        chat_id=chat_id,
        text=text,
        source=Source.MANUAL,
        expect=_expect(cls),
        idempotency_key=manual_key(key) if key is not None else None,
        risky_confirmed=confirm is not None,
        confirm_version=confirm[0] if confirm is not None else None,
        confirm_until=confirm[1] if confirm is not None else None,
    )


def click_request(
    *,
    chat_id: int,
    message_id: int,
    revision: int,
    data: str,
    cls: CommandClass,
    key: str | None,
    confirm: Confirm | None,
) -> ActionRequest:
    return ActionRequest(
        kind=ActionKind.CLICK,
        chat_id=chat_id,
        message_id=message_id,
        data=data,
        expect_revision=revision,
        source=Source.MANUAL,
        expect=_expect(cls),
        idempotency_key=manual_key(key) if key is not None else None,
        risky_confirmed=confirm is not None,
        confirm_version=confirm[0] if confirm is not None else None,
        confirm_until=confirm[1] if confirm is not None else None,
    )


class ManualControl:
    """Ручное управление аккаунтом из админки: пока оно идёт, планировщик не начинает своих
    решений. Живёт в памяти движка — перезапуск его снимает; кончается само по часам."""

    def __init__(self, clock: Clock, span: timedelta = MANUAL_CONTROL_SPAN) -> None:
        self._clock = clock
        self._span = span
        self._until: datetime | None = None

    @property
    def until(self) -> datetime | None:
        """До какого момента управление у человека; None — у планировщика."""
        until = self._until
        if until is not None and until <= self._clock.now():
            self._until = None
            return None
        return until

    def take(self) -> tuple[datetime, bool]:
        """Новый срок и признак, что управление взято сейчас (иначе — продлено)."""
        taken = self.until is None
        self._until = self._clock.now() + self._span
        return self._until, taken

    def extend(self) -> datetime | None:
        """Ручная команда продлевает идущее управление; без него — None."""
        if self.until is None:
            return None
        self._until = self._clock.now() + self._span
        return self._until

    def release(self) -> bool:
        """Управление снято сейчас; False — его и не было."""
        active = self.until is not None
        self._until = None
        return active
