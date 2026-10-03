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
    FORWARD = "forward"


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
    # Запуск сценария, шагом которого идёт действие (`actions.scenario_run_id`); у ручных
    # команд, реакций и сверки — None. В отпечаток идемпотентности (`payload`) не входит.
    scenario_run_id: int | None = None
    # Сценарий, шагом которого идёт действие: шлюз по нему допускает RISKY-клик старта сбора
    # артефакта. В отпечаток идемпотентности не входит.
    scenario: str | None = None
    # Пересылка (`FORWARD`): чат исходного сообщения `message_id`; `chat_id` — куда, `chat_title`
    # — его название по проверке Telegram (ставит шлюз; в журнале действия видно, куда ушло).
    from_chat_id: int | None = None
    chat_title: str | None = None
    # Момент по стенным часам, с которого действие не отправляется (отчёт о фабрике — полночь МСК
    # после дня битвы); сверяется перед каждой попыткой и после чтения источника пересылки. В
    # отпечаток идемпотентности не входит.
    deadline: datetime | None = None

    def payload(self) -> dict[str, object]:
        out: dict[str, object] = {
            "text": self.text,
            "message_id": self.message_id,
            "data": self.data,
            "reply_to": self.reply_to,
            "expect_revision": self.expect_revision,
            "expect_content": self.expect_content,
        }
        if self.from_chat_id is not None:
            out["from_chat_id"] = self.from_chat_id
        if self.chat_title is not None:
            out["chat_title"] = self.chat_title
        return out


@dataclass(frozen=True, slots=True)
class ActionResult:
    status: ActionStatus
    action_id: int | None = None
    reason: str = ""
    match: Match | None = None
    answer: str | None = None
