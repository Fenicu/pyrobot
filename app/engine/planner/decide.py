from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from app.engine.planner.base import BATTLE_AFTER, BATTLE_BEFORE, Step
from app.engine.planner.daily import DailyTasks
from app.engine.planner.types import Act, Candidate, Decision, WakeKind
from app.engine.settings import Settings
from app.engine.state.model import (
    DEED_PRIORS,
    ActivityStat,
    BusyState,
    CharacterState,
    PriceState,
)


class _Planner(DailyTasks):
    def decide(self) -> Decision:
        busy = self.busy()
        # Идущее дело (в т.ч. многочасовой сон) известно до `until` — старым не считается.
        if busy is None and (field := self.stale_of("busy")) is not None:
            return self.refresh("state", field) or self.wait()
        if busy is not None:
            self.wake(busy.until, "busy")
            if busy.activity.startswith("sleep_"):
                # Во сне игра позволяет только выбрать цель битвы.
                return self.battle_target(busy) or self.wait()
        steps: tuple[Step, ...] = (
            self.metro_resume,
            self.levelup,
            self.bulls,
            self.battle_target,
            self.battle_stamina,
            self.stocks_dump,
            self.factory,
            # Выбор задания занимает секунды, а ночной сон отодвинул бы его на утро.
            self.daily,
            self.lottery,
            self.sleep,
            self.book,
            self.fastfood,
            self.card,
            self.containers,
            self.prizebox,
            self.gorbushka,
            self.tangerine,
            self.smoothie,
            self.metro,
            self.deeds,
        )
        for step in steps:
            if (decision := step(busy)) is not None:
                return decision
        return self.wait()

    def levelup(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("levelup") or self.value("levelup_pending") is not True:
            return None
        if busy is not None:
            self.reject("levelup", {}, "busy")
            return None
        return self.act("levelup", {}, "levelup_pending")

    def sleep(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("sleep"):
            return None
        if (field := self.stale_of("sleep_deadline")) is not None:
            return self.refresh("sleep", field)
        deadline: datetime | None = self.value("sleep_deadline")
        if deadline is None:
            return None
        start = self.sleep_start(deadline)
        if self.now < start:
            self.wake(start, "sleep_window")
            return None
        if (allowed := self.timer("sleep_allowed_at")) is not None:
            self.reject("sleep", {}, "sleep_not_allowed")
            self.wake(allowed, "sleep_allowed")
            return None
        if busy is not None:
            self.reject("sleep", {}, "busy")
            return None
        if (field := self.stale_of("money")) is not None:
            return self.refresh("sleep", field)
        # Место выбирает сценарий по цене с экрана выбора; порог не задан — только цена.
        params = {
            "hours": self.cfg.sleep.duration_h,
            "hotel_threshold": self.cfg.sleep.hotel_if_cash_after_reserve_ge,
            "ticket_reserve": self.ticket_reserve(),
        }
        return self.act("sleep", params, "sleep_deadline")

    def book(self, busy: BusyState | None) -> Decision | None:
        return self._cooled_item("book", "books", "book_ready_at", "book_ready", busy)

    def card(self, busy: BusyState | None) -> Decision | None:
        return self._cooled_item("card", "cards", "card_ready_at", "card_ready", busy)

    def _cooled_item(
        self, name: str, count: str, ready_at: str, ready_kind: WakeKind, busy: BusyState | None
    ) -> Decision | None:
        if not self.feature_on(name):
            return None
        if (field := self.stale_of(count, ready_at)) is not None:
            return self.refresh(name, field)
        if self.value(count) <= 0:
            return None
        if (ready := self.timer(ready_at)) is not None:
            self.wake(ready, ready_kind)
            return None
        if busy is not None:
            self.reject(name, {}, "busy")
            return None
        return self.act(name, {}, f"{name}_ready")

    def fastfood(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("fastfood"):
            return None
        if (field := self.stale_of("stamina", "food_stock", "fastfood_ready_at")) is not None:
            return self.refresh("fastfood", field)
        food = self.pick_food()
        if food is None:
            return None
        if (ready := self.timer("fastfood_ready_at")) is not None:
            self.wake(ready, "fastfood_ready")
            return None
        if busy is not None and busy.activity == "eat":
            self.reject("fastfood", {"food": food}, "eating")
            return None
        return self.act("fastfood", {"food": food}, f"stamina {self.value('stamina')}")

    def containers(self, busy: BusyState | None) -> Decision | None:
        for size in ("small", "medium"):
            name = f"container_{size}"
            if not self.feature_on(name):
                return None
            if (field := self.stale_of(f"containers_{size}")) is not None:
                return self.refresh(name, field)
            if self.value(f"containers_{size}") <= 0:
                continue
            # Контейнеры во время дела игра не открывает («занят»), в отличие от коробки.
            if busy is not None:
                self.reject(name, {}, "busy")
                return None
            if act := self.act(name, {}, "have"):
                return act
        return None

    def prizebox(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("prizebox"):
            return None
        if (field := self.stale_of("prizebox", "prizebox_ready_at")) is not None:
            return self.refresh("prizebox", field)
        if self.value("prizebox") is not True:
            return None
        if (ready := self.timer("prizebox_ready_at")) is not None:
            self.wake(ready, "prizebox_ready")
            return None
        return self.act("prizebox", {}, "prizebox_ready")

    # --- платное

    def gorbushka_timer(self, at: datetime | None) -> datetime | None:
        obs = self.s.gorbushka
        if at is None or obs is None or self.due(at, obs.at):
            return None
        return at

    def gorbushka(self, busy: BusyState | None) -> Decision | None:
        name = "gorbushka"
        if not self.feature_on(name):
            return None
        g = self.gorbushka_state()
        buy = False
        if g is None:
            reason = "gorbushka_unknown"
        elif g.state == "need_ticket":
            if (field := self.stale_of("money", "knowledge")) is not None:
                return self.refresh(name, field)
            if not self.ticket_affordable():
                self.reject(name, {"buy": True}, "cant_afford")
                return None
            buy, reason = True, "buy_ticket"
        elif g.state in ("meeting", "waiting"):
            expired = g.ticket_until is not None and g.ticket_until <= self.now
            if not expired and (fight_at := self.gorbushka_timer(g.next_fight_at)) is not None:
                self.wake(fight_at, "gorbushka_next")
                return None
            reason = "ticket_expired" if expired else "gorbushka_fight"
        elif g.state == "done":
            if (comeback := self.gorbushka_timer(g.comeback_at)) is not None:
                self.wake(comeback, "gorbushka_comeback")
                return None
            reason = "gorbushka_comeback"
        else:
            return None
        if busy is not None:
            self.reject(name, {"buy": buy}, "busy")
            return None
        if (field := self.stale_of("motivation")) is not None:
            return self.refresh(name, field)
        cost = g.fight_cost if g is not None and g.fight_cost is not None else 1
        if self.value("motivation") < cost:
            self.reject(name, {"buy": buy}, "no_motivation")
            self.wake(self.value("motivation_next_at"), "motivation")
            return None
        return self.act(name, {"buy": buy}, reason)

    def score(self, activity: str, price: PriceState) -> float:
        cfg = self.cfg.strategy
        stat = self.s.activity_stats.get(activity) or DEED_PRIORS.get(activity) or ActivityStat()
        resources = stat.knowledge + stat.details + stat.raw
        value = (
            cfg.weight_xp * stat.exp / cfg.exp_scale
            + cfg.weight_money * (stat.money - price.money) / cfg.money_scale
            + cfg.weight_resources * resources / cfg.resource_scale
        )
        return value / max(price.motivation, 1)

    def deeds(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("deed:") or busy is not None:
            return None
        if (field := self.stale_of("motivation", "money", "details", "battle_at")) is not None:
            return self.refresh("deeds", field)
        ok = self.doable_deeds()
        if not ok:
            return None
        chosen, reason = (
            self.personal_deed(ok)
            or self.team_deed(ok)
            or self.focus_deed(ok)
            or self.best_deed(ok)
        )
        for candidate in ok:
            verdict = "chosen" if candidate is chosen else "ok"
            self.candidates.append(replace(candidate, verdict=verdict))
        return Act(chosen.scenario, {}, reason, tuple(self.candidates))

    def doable_deeds(self) -> list[Candidate]:
        """Разрешённые дела с вердиктом `ok`; отказанные сразу уходят в кандидаты."""
        battle = self.battle_time()
        deadline: datetime | None = self.value("sleep_deadline")
        motivation = self.value("motivation") - self.motivation_reserve() - self.metro_reserve()
        money = self.value("money") - self.ticket_reserve() - self.hotel_reserve()
        details: int = self.value("details")
        focus = self.cfg.strategy.focus
        ok: list[Candidate] = []
        for activity in self.cfg.strategy.deeds:
            name = f"deed:{activity}"
            price = self.price(activity)
            end = self.now + self.duration(activity, price)
            score = self.score(activity, price)
            params = {"today": self.done_today.get(name, 0)} if activity in focus else {}
            verdict = None
            if (
                battle is not None
                and self.now < battle + BATTLE_AFTER
                and end > battle - BATTLE_BEFORE
            ):
                verdict = "battle_window"
                self.wake(battle + BATTLE_AFTER, "battle")
            elif deadline is not None and end > deadline:
                verdict = "sleep_deadline"
            elif self.blocks_factory(end):
                verdict = "factory_window"
            elif motivation < price.motivation:
                verdict = "no_motivation"
                self.wake(self.value("motivation_next_at"), "motivation")
            elif money < price.money:
                verdict = "no_money"
            elif details < price.details:
                verdict = "no_details"
            elif score <= 0:
                verdict = "no_value"
            else:
                verdict = self.gate(name)
            if verdict is None:
                ok.append(Candidate(name, params, score, "ok"))
            else:
                self.candidates.append(Candidate(name, params, score, verdict))
        return ok

    def focus_deed(self, ok: list[Candidate]) -> tuple[Candidate, str] | None:
        """Основные дела делят 🔥 поровну: первым — сделанное сегодня меньше раз, при равенстве —
        первое по порядку `strategy.focus`."""
        order = [f"deed:{activity}" for activity in self.cfg.strategy.focus]
        ready = [c for c in ok if c.scenario in order]
        if not ready:
            return None

        def rank(c: Candidate) -> tuple[int, int]:
            return self.done_today.get(c.scenario, 0), order.index(c.scenario)

        pick = min(ready, key=rank)
        today = self.done_today.get(pick.scenario, 0)
        return pick, f"focus {pick.scenario.removeprefix('deed:')} ({today} today)"

    def best_deed(self, ok: list[Candidate]) -> tuple[Candidate, str]:
        best = max(ok, key=lambda c: c.score or 0.0)
        return best, f"best score {best.score or 0.0:.2f}"


def decide(
    state: CharacterState,
    settings: Settings,
    now: datetime,
    *,
    certified: frozenset[str] | None = None,
    last_refresh: Mapping[str, datetime] | None = None,
    cooldowns: Mapping[str, datetime] | None = None,
    last_done: Mapping[str, datetime] | None = None,
    metro_durations: Sequence[float] = (),
    done_today: Mapping[str, int] | None = None,
) -> Decision:
    """Следующий шаг: сценарий или ожидание. `certified=None` — без ограничения (dry_run).

    `last_done` — момент последнего успешного запуска каждого сценария (журнал запусков);
    `metro_durations` — длительности прошлых забегов метро в секундах (бюджет по p90);
    `done_today` — успешные запуски дел за текущий день MSK (чередование основных дел).
    """
    planner = _Planner(
        state,
        settings,
        now,
        certified,
        last_refresh or {},
        cooldowns or {},
        last_done or {},
        metro_durations,
        done_today,
    )
    return planner.decide()


def lottery_params(
    state: CharacterState,
    settings: Settings,
    now: datetime,
    *,
    certified: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Параметры `lottery_buy`, с которыми его запустил бы планировщик: билеты и запасы из
    настроек, резерв 💵 на билет Горбушки и отель на момент `now`."""
    return _Planner(state, settings, now, certified, {}, {}, {}).lottery_params()
