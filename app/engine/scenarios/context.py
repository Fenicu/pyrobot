from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum

from app.engine.bus import Delivery
from app.engine.events import Event
from app.engine.gateway.gateway import ActionGateway, Lease
from app.engine.gateway.types import (
    ActionKind,
    ActionRequest,
    ActionStatus,
    Expectation,
    Match,
    Source,
    Verdict,
)
from app.engine.parsing.refusals import Busy, Refused

Predicate = Callable[[Delivery], Match | None]


class Step(StrEnum):
    OK = "ok"
    REFUSED = "refused"
    SUPPRESSED = "suppressed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StepResult:
    step: Step
    reason: str
    delivery: Delivery | None = None

    def events(self) -> tuple[Event, ...]:
        return self.delivery.events if self.delivery is not None else ()

    def first[E: Event](self, kind: type[E]) -> E | None:
        return next((e for e in self.events() if isinstance(e, kind)), None)


class ScenarioStopped(Exception):
    """Сценарий остановлен в безопасной точке (пауза, неизвестный экран, отказ шага)."""

    def __init__(self, reason: str, result: StepResult | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.result = result


def expect_events(
    *confirm: type[Event],
    accept: Callable[[Event], bool] = lambda e: True,
    refuse: tuple[type[Event], ...] = (Refused, Busy),
) -> Predicate:
    """Подтверждает доставку с событием из `confirm`, отклоняет — с отказом игры."""

    def predicate(delivery: Delivery) -> Match | None:
        for event in delivery.events:
            if isinstance(event, confirm) and accept(event):
                return Match(Verdict.CONFIRMED, event.kind)
            if isinstance(event, refuse):
                detail = getattr(event, "reason", None) or event.kind
                return Match(Verdict.REFUSED, str(detail))
        return None

    return predicate


def expect_button(data: str) -> Predicate:
    """Подтверждает сообщение (или правку) с inline-кнопкой `data`."""

    def predicate(delivery: Delivery) -> Match | None:
        if delivery.msg.button(data) is not None:
            return Match(Verdict.CONFIRMED, data)
        return None

    return predicate


_STEP_OF = {
    ActionStatus.CONFIRMED: Step.OK,
    ActionStatus.REFUSED: Step.REFUSED,
    ActionStatus.SUPPRESSED: Step.SUPPRESSED,
}


class ScenarioContext:
    def __init__(
        self,
        gateway: ActionGateway,
        *,
        game_chat_id: int,
        simulate: bool,
        paused: Callable[[], bool],
        timeout_s: float = 20.0,
    ) -> None:
        self._gateway = gateway
        self._game = game_chat_id
        self.simulate = simulate
        self._paused = paused
        self._timeout_s = timeout_s
        self._lease: Lease | None = None

    @asynccontextmanager
    async def lease(self, owner: str) -> AsyncIterator[None]:
        lease = await self._gateway.acquire_lease(owner)
        self._lease = lease
        try:
            yield
        finally:
            self._lease = None
            await self._gateway.release_lease(lease)

    async def safe_point(self) -> None:
        # Между шагами: срочные действия могут пройти, пауза останавливает сценарий.
        if self._lease is not None:
            await self._gateway.set_safe_point(self._lease, True)
        if self._paused():
            raise ScenarioStopped("paused")

    async def send(
        self,
        text: str,
        expect: Predicate,
        *,
        chat_id: int | None = None,
        reply_to: int | None = None,
    ) -> StepResult:
        return await self._submit(
            ActionKind.SEND, expect, text=text, chat_id=chat_id, reply_to=reply_to
        )

    async def click(
        self, message_id: int, data: str, expect: Predicate, revision: int | None = None
    ) -> StepResult:
        return await self._submit(
            ActionKind.CLICK, expect, message_id=message_id, data=data, expect_revision=revision
        )

    async def _submit(
        self,
        kind: ActionKind,
        expect: Predicate,
        *,
        text: str | None = None,
        message_id: int | None = None,
        data: str | None = None,
        expect_revision: int | None = None,
        chat_id: int | None = None,
        reply_to: int | None = None,
    ) -> StepResult:
        matched: list[Delivery] = []

        def capture(delivery: Delivery) -> Match | None:
            match = expect(delivery)
            if match is not None and not matched:
                matched.append(delivery)
            return match

        if self._lease is not None:
            await self._gateway.set_safe_point(self._lease, False)
        result = await self._gateway.submit(
            ActionRequest(
                kind=kind,
                chat_id=chat_id if chat_id is not None else self._game,
                text=text,
                message_id=message_id,
                data=data,
                reply_to=reply_to,
                expect_revision=expect_revision,
                source=Source.SCENARIO,
                # Ответы на шаги сценариев всегда приходят от игры, в её чат.
                expect=Expectation(capture, self._timeout_s, chat_id=self._game),
                ttl_s=self._timeout_s * 3,
                lease_token=self._lease.token if self._lease else None,
                simulate=self.simulate,
            )
        )
        step = _STEP_OF.get(result.status, Step.FAILED)
        reason = result.match.detail if result.match is not None else result.reason
        return StepResult(step, reason, matched[0] if matched else None)
