from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.engine.bus import Delivery
from app.engine.clock import Clock, SystemClock
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
from app.engine.notify import Level, NotifierPort
from app.engine.parsing.refusals import Busy, Refused
from app.engine.settings import Settings
from app.engine.state.model import CharacterState
from app.engine.types import IncomingMessage

Predicate = Callable[[Delivery], Match | None]
# Записанные правки сообщения игрового чата (журнал).
History = Callable[[int, int], Awaitable[list[IncomingMessage]]]
# Текущая версия сообщения, прочитанная из Telegram и пропущенная через конвейер.
Reread = Callable[[int, int], Awaitable[IncomingMessage | None]]


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


def expect_edit(
    message_id: int,
    *confirm: type[Event],
    refuse: tuple[type[Event], ...] = (Refused, Busy),
) -> Predicate:
    """Как `expect_events`, но только по сообщению `message_id`: ответ на клик — правка его
    сообщения, а не другое сообщение игры, пришедшее тем временем."""
    events = expect_events(*confirm, refuse=refuse)

    def predicate(delivery: Delivery) -> Match | None:
        return events(delivery) if delivery.msg.msg_id == message_id else None

    return predicate


def expect_button(data: str, *, refuse: tuple[type[Event], ...] = ()) -> Predicate:
    """Подтверждает сообщение (или правку) с кнопкой `data`, отклоняет — событием из `refuse`."""

    def predicate(delivery: Delivery) -> Match | None:
        if delivery.msg.button(data) is not None:
            return Match(Verdict.CONFIRMED, data)
        for event in delivery.events:
            if isinstance(event, refuse):
                return Match(Verdict.REFUSED, str(getattr(event, "reason", None) or event.kind))
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
        clock: Clock | None = None,
        notifier: NotifierPort | None = None,
        history: History | None = None,
        reread: Reread | None = None,
        dry_run: bool = False,
        source: Source = Source.SCENARIO,
        run_id: int | None = None,
        scenario: str | None = None,
        state: Callable[[], CharacterState] | None = None,
        settings: Callable[[], Settings] | None = None,
        task_id: int | None = None,
    ) -> None:
        self._gateway = gateway
        # Свежие состояние и настройки между шагами: снимок на старте устаревает за порцию.
        self._state = state
        self._settings = settings
        # Задача заточки, порцию которой исполняет запуск: уходит в каждое действие шага.
        self.task_id = task_id
        # Запуск сценария: его id уходит в каждое действие шага (`actions.scenario_run_id`).
        self.run_id = run_id
        self.scenario = scenario
        # Режим запуска, зафиксированный на его старте (см. ActionRequest.dry_run).
        self.dry_run = dry_run
        # Ручной запуск из админки шлёт шаги от имени MANUAL: приоритет и правила паузы — ручные.
        self._source = source
        self._game = game_chat_id
        self.simulate = simulate
        self._paused = paused
        self._timeout_s = timeout_s
        self.clock: Clock = clock or SystemClock()
        self._notifier = notifier
        self._history = history
        self._reread = reread
        self._lease: Lease | None = None

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    def state(self) -> CharacterState:
        return self._state() if self._state is not None else CharacterState()

    def settings(self) -> Settings:
        return self._settings() if self._settings is not None else Settings()

    async def notify(self, level: Level, code: str, text: str) -> None:
        if self._notifier is not None:
            await self._notifier.notify(level, code, text)

    def latest(self, message_id: int) -> IncomingMessage | None:
        """Последняя ревизия сообщения игрового чата из кэша конвейера."""
        return self._gateway.latest(self._game, message_id)

    async def history(self, message_id: int) -> list[IncomingMessage]:
        if self._history is None:
            return []
        return await self._history(self._game, message_id)

    async def reread(self, message_id: int) -> IncomingMessage | None:
        """Текущая версия сообщения игрового чата прямо из Telegram, уже прошедшая конвейер
        (журнал, состояние, кэш ревизий); None — не прочитать."""
        if self._reread is None:
            return None
        return await self._reread(self._game, message_id)

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
        silence_confirms: bool = False,
        deadline: datetime | None = None,
    ) -> StepResult:
        """`silence_confirms` — игра отвечает только на ошибку: тишина — `Step.OK "silence"`.
        `deadline` — с этого момента шлюз команду не отправит."""
        return await self._submit(
            ActionKind.SEND,
            expect,
            text=text,
            chat_id=chat_id,
            reply_to=reply_to,
            silence_confirms=silence_confirms,
            deadline=deadline,
        )

    async def click(
        self,
        message_id: int,
        data: str,
        expect: Predicate,
        revision: int | None = None,
        *,
        content: str | None = None,
        timeout_s: float | None = None,
        deadline: datetime | None = None,
    ) -> StepResult:
        """`revision`/`content` — ревизия и хеш кадра, на котором принято решение: шлюз не
        отправит клик, если последняя правка сообщения уже другая."""
        return await self._submit(
            ActionKind.CLICK,
            expect,
            message_id=message_id,
            data=data,
            expect_revision=revision,
            expect_content=content,
            timeout_s=timeout_s,
            deadline=deadline,
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
        expect_content: str | None = None,
        chat_id: int | None = None,
        reply_to: int | None = None,
        silence_confirms: bool = False,
        timeout_s: float | None = None,
        deadline: datetime | None = None,
    ) -> StepResult:
        matched: list[Delivery] = []
        timeout = timeout_s if timeout_s is not None else self._timeout_s

        def capture(delivery: Delivery) -> Match | None:
            match = expect(delivery)
            if match is not None and not matched:
                matched.append(delivery)
            return match

        result = await self._gateway.submit(
            ActionRequest(
                kind=kind,
                chat_id=chat_id if chat_id is not None else self._game,
                text=text,
                message_id=message_id,
                data=data,
                reply_to=reply_to,
                expect_revision=expect_revision,
                expect_content=expect_content,
                source=self._source,
                # Ответы на шаги сценариев всегда приходят от игры, в её чат.
                expect=Expectation(
                    capture, timeout, chat_id=self._game, silence_confirms=silence_confirms
                ),
                ttl_s=timeout * 3,
                lease_token=self._lease.token if self._lease else None,
                simulate=self.simulate,
                dry_run=self.dry_run,
                scenario_run_id=self.run_id,
                scenario=self.scenario,
                deadline=deadline,
                task_id=self.task_id,
            )
        )
        step = _STEP_OF.get(result.status, Step.FAILED)
        reason = result.match.detail if result.match is not None else result.reason
        return StepResult(step, reason, matched[0] if matched else None)
