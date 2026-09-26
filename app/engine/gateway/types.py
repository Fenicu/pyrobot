from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
    # Шаг несертифицированного сценария: не-nav подавляется как в dry_run даже в live.
    simulate: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "text": self.text,
            "message_id": self.message_id,
            "data": self.data,
            "reply_to": self.reply_to,
            "expect_revision": self.expect_revision,
        }


@dataclass(frozen=True, slots=True)
class ActionResult:
    status: ActionStatus
    action_id: int | None = None
    reason: str = ""
    match: Match | None = None
    answer: str | None = None
