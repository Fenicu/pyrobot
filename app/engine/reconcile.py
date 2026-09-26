from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.engine.bus import Delivery
from app.engine.clock import Clock
from app.engine.events import Event
from app.engine.gateway.gateway import DATE_SKEW, RECONCILE_REASON, ActionGateway
from app.engine.gateway.store import ActionStore, Obligation
from app.engine.gateway.types import (
    ActionKind,
    ActionRequest,
    ActionStatus,
    Expectation,
    Match,
    Source,
    Verdict,
)
from app.engine.notify import NotifierPort
from app.engine.parsing.food import FoodMenu
from app.engine.parsing.gorbushka import GorbushkaScreen
from app.engine.parsing.items import GiftsScreen, Inventory
from app.engine.parsing.profile import ProfileCompact
from app.engine.settings import SettingsProvider

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RefreshSource:
    name: str
    command: str
    event: type[Event]
    fields: tuple[str, ...]


PROFILE = RefreshSource("profile", "😎Я", ProfileCompact, ("money", "motivation", "stamina"))
FOOD = RefreshSource("food", "/to_eat", FoodMenu, ("food_stock",))
INVENTORY = RefreshSource("inventory", "/inv", Inventory, ("books", "cards"))
GIFTS = RefreshSource("gifts", "/gifts", GiftsScreen, ("containers_small", "containers_medium"))
GORBUSHKA = RefreshSource("gorbushka", "/gorbushka", GorbushkaScreen, ("gorbushka",))
_FOOD_COMMANDS = frozenset({"🌭Хот-дог", "🍕Пицца", "🍔Бургер", "🍌Банан", "/eat", "🍴Есть"})
_INVENTORY_COMMANDS = frozenset({"/read_exp", "/use_card", "/unbox"})


def sources_for(obligation: Obligation) -> tuple[RefreshSource, ...]:
    """Экраны, которые показывают поля, затронутые действием; профиль — всегда."""
    text, data = obligation.text or "", obligation.data or ""
    if text in _FOOD_COMMANDS:
        return (PROFILE, FOOD)
    if text in _INVENTORY_COMMANDS:
        return (PROFILE, INVENTORY)
    if text.startswith("/unbox_"):
        return (PROFILE, GIFTS)
    if data.startswith("gorbushka_"):
        return (PROFILE, GORBUSHKA)
    return (PROFILE,)


def _seen(event: type[Event]) -> Callable[[Delivery], Match | None]:
    def predicate(delivery: Delivery) -> Match | None:
        if any(isinstance(e, event) for e in delivery.events):
            return Match(Verdict.CONFIRMED, event.kind)
        return None

    return predicate


class Reconciler:
    def __init__(
        self,
        *,
        gateway: ActionGateway,
        store: ActionStore,
        state: Callable[[], Mapping[str, Any]],
        notifier: NotifierPort,
        settings: SettingsProvider,
        clock: Clock,
        ready: Callable[[], bool],
        game_chat_id: int,
        poll_s: float = 5.0,
        max_backoff_s: float = 1800.0,
        timeout_s: float = 30.0,
        stuck_after: int = 3,
    ) -> None:
        self._gateway = gateway
        self._store = store
        self._state = state
        self._notifier = notifier
        self._settings = settings
        self._clock = clock
        self._ready = ready
        self._game = game_chat_id
        self._poll_s = poll_s
        self._max_backoff_s = max_backoff_s
        self._timeout_s = timeout_s
        self._stuck_after = stuck_after
        # Сообщённые шлюзом неопределённые действия: страховка на случай, когда их
        # итоговый статус не записался в БД.
        self._noted: list[Obligation] = []
        self._generation = 0
        self._wake = asyncio.Event()

    def note(self, req: ActionRequest, action_id: int | None) -> None:
        self._noted.append(Obligation(action_id, req.kind.value, req.text, req.data))
        self._generation += 1
        self._wake.set()

    async def pending(self) -> list[Obligation]:
        stored = await self._store.unreconciled()
        ids = {o.action_id for o in stored}
        extra = [o for o in self._noted if o.action_id is None or o.action_id not in ids]
        return [*stored, *extra]

    async def override(self) -> None:
        generation, noted = self._generation, list(self._noted)
        stored = await self._store.unreconciled()
        await self._store.mark_reconciled(
            sorted({o.action_id for o in (*stored, *noted) if o.action_id is not None})
        )
        taken = {id(o) for o in noted}
        self._noted = [o for o in self._noted if id(o) not in taken]
        # Неопределённое действие, сообщённое во время override, оставляет блок до сверки.
        if self._generation == generation:
            await self._gateway.allow_spending()

    async def run(self) -> None:
        failures = 0
        while True:
            if self._gateway.spending_blocked != RECONCILE_REASON or not self._ready():
                await self._pause(self._poll_s)
                continue
            obligations = await self.pending()
            refreshed = await self._refresh_all(obligations)
            if refreshed is None:
                await self._pause(self._poll_s)
                continue
            if refreshed:
                failures = 0
                await self._settle(obligations)
                continue
            failures += 1
            if failures == self._stuck_after:
                await self._notifier.notify(
                    "warn",
                    "reconcile_stuck",
                    f"state refresh failed {failures} times; spending stays blocked",
                )
            base = self._settings.current.engine.refresh_min_interval_s
            await self._pause(min(base * 2 ** (failures - 1), self._max_backoff_s))

    async def _pause(self, seconds: float) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), seconds)
        except TimeoutError:
            pass

    async def _refresh_all(self, obligations: list[Obligation]) -> bool | None:
        """None — попытка не состоялась (шлюз остановлен) и неудачей не считается."""
        sources: list[RefreshSource] = [PROFILE]
        for obligation in obligations:
            sources.extend(s for s in sources_for(obligation) if s not in sources)
        refreshed: list[tuple[RefreshSource, datetime]] = []
        for source in sources:
            requested_at = self._clock.now()
            confirmed = await self._request(source)
            if confirmed is None:
                return None
            if not confirmed or not self._fresh(source, requested_at):
                return False
            refreshed.append((source, requested_at))
        # Экран, пришедший позже, мог сделать сомнительным поле уже обновлённого источника.
        return all(self._fresh(source, requested_at) for source, requested_at in refreshed)

    async def _request(self, source: RefreshSource) -> bool | None:
        result = await self._gateway.submit(
            ActionRequest(
                kind=ActionKind.SEND,
                chat_id=self._game,
                text=source.command,
                source=Source.SCENARIO,
                expect=Expectation(_seen(source.event), self._timeout_s),
                ttl_s=self._timeout_s * 4,
            )
        )
        if result.status is ActionStatus.CONFIRMED:
            return True
        if result.status is ActionStatus.SUPPRESSED and result.reason == "shutdown":
            return None
        log.warning("reconcile %s: %s %s", source.name, result.status.value, result.reason)
        return False

    def _fresh(self, source: RefreshSource, requested_at: datetime) -> bool:
        # Событие получено, но состояние должно его учесть: редьюсер мог упасть.
        state = self._state()
        for name in source.fields:
            field = state.get(name)
            if not isinstance(field, Mapping) or field.get("src") == "doubtful":
                return False
            if datetime.fromisoformat(str(field["at"])) < requested_at - DATE_SKEW:
                return False
        return True

    async def _settle(self, done: list[Obligation]) -> None:
        await self._store.mark_reconciled([o.action_id for o in done if o.action_id])
        self._noted = [o for o in self._noted if o not in done]
        if await self.pending():
            return
        if self._gateway.spending_blocked == RECONCILE_REASON:
            await self._gateway.allow_spending()
            await self._notifier.notify(
                "info",
                "reconciled_auto",
                "state refreshed after uncertain actions; spending unblocked",
            )
