from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum

from app.engine.bus import Delivery


class Source(IntEnum):
    URGENT = 0
    MANUAL = 1
    SCENARIO = 2
    PLANNER = 3


class ActionKind(StrEnum):
    SEND = "send"
    CLICK = "click"


class ActionStatus(StrEnum):
    INTENT = "intent"
    SENT = "sent"
    CONFIRMED = "confirmed"
    REFUSED = "refused"
    SUPPRESSED = "suppressed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    REJECTED = "rejected"


class Verdict(StrEnum):
    CONFIRMED = "confirmed"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class Match:
    verdict: Verdict
    detail: str = ""


Predicate = Callable[[Delivery], Match | None]


@dataclass(frozen=True, slots=True)
class Expectation:
    predicate: Predicate
    timeout_s: float | None = None
    # Чат ответа, если он другой: /gt уходит в чат Tangerine, ошибка приходит от игры.
    chat_id: int | None = None
    # Игра отвечает только на ошибку: тишина до тайм-аута — подтверждение, а не неизвестный исход.
    silence_confirms: bool = False


@dataclass(frozen=True, slots=True)
class ActionRequest:
    kind: ActionKind
    chat_id: int
    text: str | None = None
    message_id: int | None = None
    data: str | None = None
    reply_to: int | None = None
    source: Source = Source.PLANNER
    expect: Expectation | None = None
    ttl_s: float | None = None
    idempotency_key: str | None = None
    lease_token: str | None = None
    risky_confirmed: bool = False
    expect_revision: int | None = None
    # Хеш содержимого кадра, на котором принято решение: правки одной секунды не различить по
    # ревизии, а кнопка на новом кадре может остаться прежней.
    expect_content: str | None = None
    # Шаг несертифицированного сценария: не-nav подавляется как в dry_run даже в live.
    simulate: bool = False
    # Шаг запуска, начатого в dry_run: режим запуска зафиксирован на старте, переключение
    # в live посреди сценария не делает его следующие шаги реальными.
    dry_run: bool = False
    # Подтверждение risky: версия состояния и срок, на которые выдан токен. Шлюз сверяет их перед
    # каждой попыткой отправки — пока действие ждало в очереди, состояние могло измениться.
    confirm_version: int | None = None
    confirm_until: datetime | None = None

    def payload(self) -> dict[str, object]:
        return {
            "text": self.text,
            "message_id": self.message_id,
            "data": self.data,
            "reply_to": self.reply_to,
            "expect_revision": self.expect_revision,
            "expect_content": self.expect_content,
        }


@dataclass(frozen=True, slots=True)
class ActionResult:
    status: ActionStatus
    action_id: int | None = None
    reason: str = ""
    match: Match | None = None
    answer: str | None = None
