from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from app.engine.planner.types import Act, Candidate, Decision, Wait
from app.engine.settings import Settings
from app.engine.state.model import (
    DEED_PRIORS,
    DEFAULT_PRICES,
    ActivityStat,
    BusyState,
    CharacterState,
    GorbushkaState,
    PriceState,
    TeamTask,
    stale_fields,
)

# «Скоро Битва» замечено до ~6 мин до начала, «Битва уже началась» — в первую минуту.
BATTLE_BEFORE = timedelta(minutes=6)
BATTLE_AFTER = timedelta(minutes=1)
GORBUSHKA_AHEAD = timedelta(hours=1)
# Деньги на отель дела не тратят за столько до окна сна.
HOTEL_RESERVE_AHEAD = timedelta(hours=3)
GORBUSHKA_TICKET = PriceState(money=120, knowledge=20)
UNKNOWN_DEED_MINUTES = 10

_PROFILE = (
    "level",
    "money",
    "stamina",
    "knowledge",
    "raw",
    "details",
    "motivation",
    "motivation_next_at",
    "battle_at",
    "busy",
    "sleep_deadline",
    "sleep_allowed_at",
)
SOURCE = {
    **dict.fromkeys(_PROFILE, "profile"),
    **dict.fromkeys(
        ("books", "cards", "book_ready_at", "card_ready_at", "prizebox", "prizebox_ready_at"),
        "inventory",
    ),
    **dict.fromkeys(("food_stock", "fastfood_ready_at"), "food"),
    **dict.fromkeys(("containers_small", "containers_medium"), "gifts"),
    "gorbushka": "gorbushka",
}
FEATURE = {
    "book": "books",
    "card": "cards_containers",
    "prizebox": "cards_containers",
    "container_small": "cards_containers",
    "container_medium": "cards_containers",
    "fastfood": "fastfood",
    "levelup": "levelup",
    "gorbushka": "gorbushka",
    "sleep": "sleep",
}
TEAM_RESOURCE = {"💡": "exp", "💵": "money", "📚": "knowledge", "⚙️": "details", "🔩": "raw"}

Step = Callable[[BusyState | None], Decision | None]


class _Planner:
    def __init__(
        self,
        state: CharacterState,
        settings: Settings,
        now: datetime,
        certified: frozenset[str] | None,
        last_refresh: Mapping[str, datetime],
        cooldowns: Mapping[str, datetime],
    ) -> None:
        self.s = state
        self.cfg = settings
        self.now = now
        self.certified = certified
        self.last_refresh = last_refresh
        self.cooldowns = cooldowns
        volatile = timedelta(minutes=settings.engine.state_stale_after_min)
        stale = set(stale_fields(state, now, volatile))
        # После тика регенерации 🔥 наблюдение мотивации устарело независимо от возраста.
        regen = state.motivation_next_at
        seen = state.motivation
        if seen is not None and regen is not None and regen.value is not None:
            if seen.at < regen.value <= now:
                stale.add("motivation")
        # Прошедшая битва: время следующей известно только из свежего профиля.
        battle = state.battle_at
        if battle is not None and battle.value + BATTLE_AFTER <= now:
            stale.add("battle_at")
        self.stale = frozenset(stale)
        self.refresh_every = timedelta(seconds=settings.engine.refresh_min_interval_s)
        self.candidates: list[Candidate] = []
        self.wakeups: list[tuple[datetime, str]] = []

    # --- общее

    def value(self, name: str) -> Any:
        obs = getattr(self.s, name)
        return None if obs is None else obs.value

    def wake(self, at: datetime | None, reason: str) -> None:
        if at is not None and at > self.now:
            self.wakeups.append((at, reason))

    def reject(self, scenario: str, params: Mapping[str, Any], verdict: str) -> None:
        self.candidates.append(Candidate(scenario, dict(params), None, verdict))

    def feature_on(self, scenario: str) -> bool:
        feature = "deeds" if scenario.startswith("deed:") else FEATURE.get(scenario)
        return feature is None or bool(getattr(self.cfg.features, feature))

    def gate(self, scenario: str) -> str | None:
        if self.certified is not None and scenario not in self.certified:
            return "uncertified"
        until = self.cooldowns.get(scenario)
        if until is not None and until > self.now:
            self.wake(until, f"cooldown:{scenario}")
            return "cooldown"
        return None

    def stale_of(self, *fields: str) -> str | None:
        for name in fields:
            if getattr(self.s, name) is None or name in self.stale:
                return name
        return None

    def act(
        self, scenario: str, params: Mapping[str, Any], reason: str, score: float | None = None
    ) -> Act | None:
        if (why := self.gate(scenario)) is not None:
            self.reject(scenario, params, why)
            return None
        self.candidates.append(Candidate(scenario, dict(params), score, "chosen"))
        return Act(scenario, dict(params), reason, tuple(self.candidates))

    def refresh(self, scenario: str, field: str) -> Act | None:
        """Нужное поле неизвестно или устарело: обновить источник, если позволяет лимит."""
        source = SOURCE[field]
        self.reject(scenario, {}, f"stale:{field}")
        last = self.last_refresh.get(source)
        if last is not None and self.now - last < self.refresh_every:
            self.wake(last + self.refresh_every, f"refresh:{source}")
            return None
        return self.act("refresh", {"source": source}, f"{scenario} needs {field}")

    def wait(self) -> Wait:
        if not self.wakeups:
            return Wait(None, "no_timers", tuple(self.candidates))
        at, reason = min(self.wakeups)
        return Wait(at, reason, tuple(self.candidates))

    # --- решение

    def decide(self) -> Decision:
        busy = self.busy()
        # Идущее дело (в т.ч. многочасовой сон) известно до `until` — старым не считается.
        if busy is None and (field := self.stale_of("busy")) is not None:
            return self.refresh("state", field) or self.wait()
        if busy is not None:
            self.wake(busy.until, "busy")
            if busy.activity.startswith("sleep_"):
                return self.wait()
        steps: tuple[Step, ...] = (
            self.levelup,
            self.sleep,
            self.book,
            self.fastfood,
            self.card,
            self.containers,
            self.prizebox,
            self.gorbushka,
            self.deeds,
        )
        for step in steps:
            if (decision := step(busy)) is not None:
                return decision
        return self.wait()

    def busy(self) -> BusyState | None:
        obs = self.s.busy
        if obs is None or obs.src == "doubtful" or obs.value is None:
            return None
        return obs.value if obs.value.until > self.now else None

    def levelup(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("levelup") or self.value("levelup_pending") is not True:
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
        start = deadline - timedelta(minutes=self.cfg.sleep.lead_min)
        if self.now < start:
            self.wake(start, "sleep_window")
            return None
        allowed: datetime | None = self.value("sleep_allowed_at")
        if allowed is not None and self.now < allowed:
            self.reject("sleep", {}, "sleep_not_allowed")
            self.wake(allowed, "sleep_allowed")
            return None
        if busy is not None:
            self.reject("sleep", {}, "busy")
            return None
        if (field := self.stale_of("money")) is not None:
            return self.refresh("sleep", field)
        params = {"hours": self.cfg.sleep.duration_h, "hotel": self.hotel()}
        return self.act("sleep", params, "sleep_deadline")

    def hotel_cost(self) -> int | None:
        known = self.s.prices.get("hotel")
        level: int | None = self.value("level")
        if known is not None:
            return known.value.money
        return 3 * level if level is not None else None

    def hotel(self) -> bool:
        threshold = self.cfg.sleep.hotel_if_cash_after_reserve_ge
        if threshold is None:
            threshold = self.hotel_cost()
        if threshold is None or self.value("money") is None:
            return False
        return int(self.value("money")) - self.ticket_reserve() >= threshold

    def book(self, busy: BusyState | None) -> Decision | None:
        return self._cooled_item("book", "books", "book_ready_at", busy)

    def card(self, busy: BusyState | None) -> Decision | None:
        return self._cooled_item("card", "cards", "card_ready_at", busy)

    def _cooled_item(
        self, name: str, count: str, ready_at: str, busy: BusyState | None
    ) -> Decision | None:
        if not self.feature_on(name):
            return None
        if (field := self.stale_of(count, ready_at)) is not None:
            return self.refresh(name, field)
        if self.value(count) <= 0:
            return None
        ready: datetime = self.value(ready_at)
        if ready > self.now:
            self.wake(ready, f"{name}_ready")
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
        ready: datetime = self.value("fastfood_ready_at")
        if ready > self.now:
            self.wake(ready, "fastfood_ready")
            return None
        if busy is not None and busy.activity == "eat":
            self.reject("fastfood", {"food": food}, "eating")
            return None
        return self.act("fastfood", {"food": food}, f"stamina {self.value('stamina')}")

    def pick_food(self) -> str | None:
        stamina: int = self.value("stamina")
        stock = self.value("food_stock")
        for kind in self.cfg.food.order:
            item = stock.get(kind)
            reserve = self.cfg.food.banana_reserve if kind == "banana" else 0
            if item is not None and item.count > reserve and stamina < item.low:
                return kind
        return None

    def containers(self, busy: BusyState | None) -> Decision | None:
        for size in ("small", "medium"):
            name = f"container_{size}"
            if not self.feature_on(name):
                return None
            if (field := self.stale_of(f"containers_{size}")) is not None:
                return self.refresh(name, field)
            if self.value(f"containers_{size}") > 0 and (act := self.act(name, {}, "have")):
                return act
        return None

    def prizebox(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("prizebox"):
            return None
        if (field := self.stale_of("prizebox", "prizebox_ready_at")) is not None:
            return self.refresh("prizebox", field)
        if self.value("prizebox") is not True:
            return None
        ready: datetime | None = self.value("prizebox_ready_at")
        if ready is not None and ready > self.now:
            self.wake(ready, "prizebox_ready")
            return None
        return self.act("prizebox", {}, "prizebox_ready")

    # --- платное

    def gorbushka_state(self) -> GorbushkaState | None:
        if self.stale_of("gorbushka") is not None:
            return None
        state: GorbushkaState = self.value("gorbushka")
        return state

    def ticket(self) -> PriceState:
        known = self.s.prices.get("gorbushka_ticket")
        return known.value if known is not None else GORBUSHKA_TICKET

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
            ticket = self.ticket()
            if self.value("money") < ticket.money or self.value("knowledge") < ticket.knowledge:
                self.reject(name, {"buy": True}, "cant_afford")
                return None
            buy, reason = True, "buy_ticket"
        elif g.state in ("meeting", "waiting"):
            fight_at = g.next_fight_at or self.now
            expired = g.ticket_until is not None and g.ticket_until <= self.now
            if not expired and fight_at > self.now:
                self.wake(fight_at, "gorbushka_next")
                return None
            reason = "ticket_expired" if expired else "gorbushka_fight"
        elif g.state == "done":
            if g.comeback_at is not None and g.comeback_at > self.now:
                self.wake(g.comeback_at, "gorbushka_comeback")
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

    def motivation_reserve(self) -> int:
        g = self.gorbushka_state() if self.feature_on("gorbushka") else None
        if g is None or g.state not in ("meeting", "waiting"):
            return 0
        if g.won is not None and g.total is not None and g.won >= g.total:
            return 0
        fight_at = g.next_fight_at or self.now
        if fight_at - self.now > GORBUSHKA_AHEAD:
            return 0
        return g.fight_cost if g.fight_cost is not None else 1

    def ticket_reserve(self) -> int:
        g = self.gorbushka_state() if self.feature_on("gorbushka") else None
        return self.ticket().money if g is not None and g.state == "need_ticket" else 0

    def money_reserve(self) -> int:
        reserve = self.ticket_reserve()
        deadline: datetime | None = self.value("sleep_deadline")
        if not self.feature_on("sleep") or deadline is None:
            return reserve
        window = deadline - timedelta(minutes=self.cfg.sleep.lead_min) - HOTEL_RESERVE_AHEAD
        cost = self.hotel_cost()
        if self.now >= window and cost is not None and self.hotel():
            reserve += cost
        return reserve

    def price(self, activity: str) -> PriceState:
        known = self.s.prices.get(activity)
        default = DEFAULT_PRICES.get(activity, PriceState(motivation=1))
        if known is None:
            return default
        if f"prices.{activity}" not in self.stale:
            return known.value
        # Устаревшая цена могла вырасти: берём большее из запомненного и умолчания.
        worst = {
            f: max(getattr(known.value, f), getattr(default, f)) for f in PriceState.model_fields
        }
        return PriceState(**worst)

    def duration(self, activity: str, price: PriceState) -> timedelta:
        default = DEFAULT_PRICES.get(activity)
        minutes = price.minutes or (default.minutes if default else 0) or UNKNOWN_DEED_MINUTES
        return timedelta(minutes=minutes)

    def team(self) -> TeamTask | None:
        task: TeamTask | None = self.value("team_task")
        if task is None or "team_task" in self.stale or task.current >= task.goal:
            return None
        return task

    def score(self, activity: str, price: PriceState) -> float:
        cfg = self.cfg.strategy
        stat = self.s.activity_stats.get(activity) or DEED_PRIORS.get(activity) or ActivityStat()
        resources = stat.knowledge + stat.details + stat.raw
        value = (
            cfg.weight_xp * stat.exp / cfg.exp_scale
            + cfg.weight_money * (stat.money - price.money) / cfg.money_scale
            + cfg.weight_resources * resources / cfg.resource_scale
        )
        task = self.team()
        if task is not None and (field := TEAM_RESOURCE.get(task.resource)) is not None:
            scale = {"exp": cfg.exp_scale, "money": cfg.money_scale}.get(field, cfg.resource_scale)
            value += cfg.weight_team * getattr(stat, field) / scale
        return value / max(price.motivation, 1)

    def deeds(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("deed:") or busy is not None:
            return None
        if (field := self.stale_of("motivation", "money", "details", "battle_at")) is not None:
            return self.refresh("deeds", field)
        battle: datetime = self.value("battle_at")
        deadline: datetime | None = self.value("sleep_deadline")
        motivation = self.value("motivation") - self.motivation_reserve()
        money = self.value("money") - self.money_reserve()
        details: int = self.value("details")
        ok: list[Candidate] = []
        for activity in self.cfg.strategy.deeds:
            name = f"deed:{activity}"
            price = self.price(activity)
            end = self.now + self.duration(activity, price)
            score = self.score(activity, price)
            verdict = None
            if self.now < battle + BATTLE_AFTER and end > battle - BATTLE_BEFORE:
                verdict = "battle_window"
                self.wake(battle + BATTLE_AFTER, "battle")
            elif deadline is not None and end > deadline:
                verdict = "sleep_deadline"
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
                ok.append(Candidate(name, {}, score, "ok"))
            else:
                self.candidates.append(Candidate(name, {}, score, verdict))
        if not ok:
            return None
        best = max(ok, key=lambda c: c.score or 0.0)
        for candidate in ok:
            chosen = candidate is best
            self.candidates.append(
                Candidate(candidate.scenario, {}, candidate.score, "chosen" if chosen else "ok")
            )
        return Act(best.scenario, {}, f"best score {best.score:.2f}", tuple(self.candidates))


def decide(
    state: CharacterState,
    settings: Settings,
    now: datetime,
    *,
    certified: frozenset[str] | None = None,
    last_refresh: Mapping[str, datetime] | None = None,
    cooldowns: Mapping[str, datetime] | None = None,
) -> Decision:
    """Следующий шаг: сценарий или ожидание. `certified=None` — без ограничения (dry_run)."""
    planner = _Planner(state, settings, now, certified, last_refresh or {}, cooldowns or {})
    return planner.decide()
