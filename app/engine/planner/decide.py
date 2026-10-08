from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Literal

from app.engine.gametime import to_msk
from app.engine.parsing.gifts import TANGERINE_GIFT_PRICE
from app.engine.parsing.trips import TRIP_SPAN
from app.engine.planner.base import (
    READY_SLACK,
    SOURCE,
    TIMER_MARGIN,
    Step,
)
from app.engine.planner.daily import DailyTasks
from app.engine.planner.obligations import (
    DUMP_SPAN,
    FACTORY_CLOSE,
    FACTORY_REPORT_UNTIL,
    LOTTERY_LAST_START,
    TARGET_LAST_CALL,
    msk_at,
)
from app.engine.planner.types import Act, Candidate, Decision, Reserve, WakeKind, Wakeup
from app.engine.settings import Settings
from app.engine.state.model import (
    DEED_PRIORS,
    ActivityStat,
    BusyState,
    CharacterState,
    Obs,
    PriceState,
    TripsState,
    VehicleState,
)
from app.engine.state.reducer import LOTTERY_CURRENCIES, TRIP_RESULT_GRACE
from app.engine.trips import trip_vehicles

Phase = Literal["unknown", "asleep", "busy", "free"]
# Таймеры-моменты: наступившие во сне к подъёму теряют смысл — битва, выброс из метро и конец
# окна-запрета пройдут, окно сна относится к ночи, которую персонаж уже спит.
MOMENTS: frozenset[WakeKind] = frozenset({"battle", "metro_kick", "sleep_window", "gear_guard"})


NextWhy = Literal["personal", "team", "focus", "best", "artifact"]
# Во время сбора экран артефактов перечитывается раз в 3 часа: таймер и уровни.
ARTIFACT_REREAD = timedelta(hours=3)
# Экран «Транспорт» старше 6 часов перечитывается: новые сезонные виды, цены.
TRIPS_MAX_AGE = timedelta(hours=6)
# «Пилить»: раздел стартапов открыт с 18🎚; за заход уходит до 7📚; 🔩 — 2 до 7-го уровня стартапа,
# дальше 3.
STARTUP_MIN_LEVEL = 18
STARTUP_KNOWLEDGE = 7
STARTUP_RAW_FALLBACK = 3
STARTUP_RAW_LEVEL = 7
STARTUP_RAW_LOW = 2


@dataclass(frozen=True, slots=True)
class NextDeed:
    """Дело, которое шаг дел выбрал бы среди доступных сейчас, и почему: под личное или
    командное задание, основное по очереди или лучшее по оценке."""

    deed: str
    why: NextWhy


@dataclass(frozen=True, slots=True)
class PlanHints:
    """Подробности для строк плана: цель ближайшей битвы, билеты лотереи по настройкам, длина
    и место сна на текущих деньгах, дело, которое шаг дел взял бы следующим среди доступных
    сейчас (None — неизвестно или ни одно не доступно)."""

    battle_target: str | None
    lottery_tickets: dict[str, int | Literal["max"]]
    sleep_hours: int
    sleep_place: Literal["hotel", "bridge"] | None
    next_deed: NextDeed | None


@dataclass(frozen=True, slots=True)
class Basis:
    """Занятость устарела, но известна: план сверх решения — по последним известным значениям.

    `since` — старейшее из наблюдений, которые второй проход взял как свежие (быстрые поля, которые
    читает планировщик); `busy_at` — наблюдение занятости; `ended` — дело, которое тогда шло и уже
    кончилось (свободен с его `until`: дела начинает только бот), None — тогда был свободен;
    `considered` — кандидаты второго прохода до его первого решения, без его «выбрано»."""

    since: datetime
    busy_at: datetime
    ended: BusyState | None
    considered: tuple[Candidate, ...]


@dataclass(frozen=True, slots=True)
class Outlook:
    """Проход планировщика для «Плана бота»: решение — то же, что у `decide`, плюс то, что
    осталось за ним на том же снимке.

    `considered` — кандидаты до первого решения (его `candidates`); `also_ready` — сценарии,
    которые дальше по проходу тоже вернули бы действие (не очередь: запуск меняет состояние);
    `wakeups` — таймеры всего прохода; `after_wake` — во сне таймеры прохода «как после
    пробуждения»; `focus` — основные дела и их запуски за день; `reserves` — 🔥, которые дела
    сейчас не тратят (под бой Горбушки, вход в метро).

    `basis` — занятость устарела, но известна: решение — обновить её, а `also_ready`, `wakeups`,
    `hints`, `reserves` и `basis.considered` — второй проход по последним известным значениям.
    """

    phase: Phase
    busy: BusyState | None
    decision: Decision
    considered: tuple[Candidate, ...]
    also_ready: tuple[Act, ...]
    wakeups: tuple[Wakeup, ...]
    after_wake: tuple[Wakeup, ...]
    focus: tuple[tuple[str, int], ...]
    hints: PlanHints
    reserves: tuple[Reserve, ...]
    basis: Basis | None = None


class _Planner(DailyTasks):
    def decide(self) -> Decision:
        if (probe := self.metro_probe()) is not None:
            return probe
        busy = self.busy()
        # Идущее дело (в т.ч. многочасовой сон) известно до `until` — старым не считается.
        if busy is None and (field := self.stale_of("busy")) is not None:
            return self.refresh("state", field) or self.wait()
        if busy is not None:
            self.wake(busy.until, "busy")
            if busy.activity.startswith("sleep_"):
                # Во сне игра позволяет только выбрать цель битвы.
                return self.battle_target(busy) or self.wait()
        for step in self.steps():
            if (decision := step(busy)) is not None:
                return decision
        return self.wait()

    def outlook(self, fresh: Callable[[], _Planner]) -> Outlook:
        """Ветки занятости — как в `decide`; `fresh` — такой же планировщик с чистыми
        кандидатами и таймерами для прохода «после пробуждения» и подсказок."""
        if (probe := self.metro_probe()) is not None:
            busy = self.busy()
            return self.view("free" if busy is None else "busy", busy, probe, (), fresh)
        busy = self.busy()
        if busy is None and (field := self.stale_of("busy")) is not None:
            decision = self.refresh("state", field) or self.wait()
            seen = self.s.busy
            if seen is None or seen.src == "doubtful":
                return self.view("unknown", None, decision, (), fresh)
            return self.last_known(decision, seen, fresh)
        if busy is not None:
            self.wake(busy.until, "busy")
            if busy.activity.startswith("sleep_"):
                decision = self.battle_target(busy) or self.wait()
                later = fresh()
                for step in later.steps():
                    step(None)
                woke = self.after_wake(later.wakeups, busy.until + TIMER_MARGIN)
                return self.view("asleep", busy, decision, (), fresh, woke)
        return self.awake(busy, fresh)

    def last_known(
        self, decision: Decision, seen: Obs[BusyState | None], fresh: Callable[[], _Planner]
    ) -> Outlook:
        """Занятость устарела, но известна: наблюдалось «свободен» или дело, которое уже кончилось
        (идущее до `until` не устаревает), — сейчас свободен. Решение — обновить её, остальное —
        второй проход на последних известных значениях. Первое действие второго прохода — не
        решение цикла: оно в `also_ready` вместе с прочими, кроме самого решения."""

        def then() -> _Planner:
            planner = fresh()
            planner.stale = planner.find_stale(last_known=True)
            return planner

        later = then()
        view = later.awake(later.busy(), then)
        # Взятые как свежие — устаревшие сейчас, но не во втором проходе (занятость — всегда).
        taken = (self.stale - later.stale) & SOURCE.keys()
        since = min(getattr(self.s, name).at for name in taken)
        own = run_key(decision) if isinstance(decision, Act) else None
        first = (view.decision,) if isinstance(view.decision, Act) else ()
        considered = tuple(c for c in view.considered if c.verdict != "chosen")
        return Outlook(
            "unknown",
            None,
            decision,
            decision.candidates,
            tuple(a for a in first + view.also_ready if run_key(a) != own),
            earliest((*self.wakeups, *view.wakeups)),
            (),
            view.focus,
            view.hints,
            view.reserves,
            Basis(since, seen.at, seen.value, considered),
        )

    def awake(self, busy: BusyState | None, fresh: Callable[[], _Planner]) -> Outlook:
        """Проход всех шагов, как у `decide`: первое решение и то, что готово за ним."""
        first: Decision | None = None
        ready: dict[str, Act] = {}
        for step in self.steps():
            found = step(busy)
            if found is None:
                continue
            if first is None:
                first = found
            elif isinstance(found, Act):
                ready.setdefault(run_key(found), found)
        decision = first or self.wait()
        own = run_key(decision) if isinstance(decision, Act) else None
        also = tuple(act for key, act in ready.items() if key != own)
        return self.view("free" if busy is None else "busy", busy, decision, also, fresh)

    def view(
        self,
        phase: Phase,
        busy: BusyState | None,
        decision: Decision,
        also_ready: tuple[Act, ...],
        fresh: Callable[[], _Planner],
        later: Iterable[Wakeup] = (),
    ) -> Outlook:
        wakeups = earliest(self.wakeups)
        known = {(w.kind, w.key) for w in wakeups}
        after = tuple(w for w in earliest(later) if (w.kind, w.key) not in known)
        focus = tuple(
            (name, self.done_today.get(name, 0))
            for name in (f"deed:{deed}" for deed in self.cfg.strategy.focus)
        )
        return Outlook(
            phase,
            busy,
            decision,
            decision.candidates,
            also_ready,
            wakeups,
            after,
            focus,
            self.hints(fresh().next_deed()),
            self.reserves(),
        )

    def hints(self, next_deed: NextDeed | None) -> PlanHints:
        battle = self.upcoming_battle()
        tickets = self.cfg.lottery.tickets
        return PlanHints(
            battle_target=self.target_for(battle) if battle is not None else None,
            lottery_tickets={c: getattr(tickets, c) for c in LOTTERY_CURRENCIES},
            sleep_hours=self.cfg.sleep.duration_h,
            sleep_place=self.sleep_place(),
            next_deed=next_deed,
        )

    def after_wake(self, wakeups: Iterable[Wakeup], woke: datetime) -> tuple[Wakeup, ...]:
        """Таймеры прохода «после пробуждения». Наступающий во сне случится при подъёме
        (`at = woke`): готовность — всегда, окно (продажа лотереи, запись на фабрику, слив
        налички) — если к подъёму оно ещё открыто; закрытые окна и моменты (`MOMENTS`)
        отбрасываются."""
        out: list[Wakeup] = []
        for w in wakeups:
            if w.at >= woke:
                out.append(w)
            elif w.kind in MOMENTS:
                continue
            elif (end := self.window_end(w)) is None or end > woke:
                out.append(replace(w, at=woke))
        return earliest(out)

    def window_end(self, w: Wakeup) -> datetime | None:
        """Конец окна, которое открывает таймер `w`; None — таймер не окно, а готовность."""
        start = w.at - TIMER_MARGIN
        if w.kind == "lottery_open":
            return msk_at(start, LOTTERY_LAST_START)
        if w.kind == "factory_open":
            return msk_at(start, FACTORY_CLOSE)
        if w.kind == "factory_report":
            return msk_at(start, FACTORY_REPORT_UNTIL)
        if w.kind == "stocks_dump":
            lead = timedelta(minutes=self.cfg.stocks.dump_lead_min)
            return start + lead + DUMP_SPAN - TARGET_LAST_CALL
        return None

    def next_deed(self) -> NextDeed | None:
        """Дело, которое шаг дел выбрал бы сейчас тем же порядком (личное задание, командное,
        основное по очереди, лучшее по оценке), если бы персонаж был свободен; None — ни одно не
        доступно или нужные поля устарели. Считается на отдельном планировщике: его отказы и
        таймеры в план не попадают."""
        if not self.feature_on("deed:"):
            return None
        if self.stale_of("motivation", "money", "details", "battle_at") is not None:
            return None
        ok = self.doable_deeds()
        if not ok:
            return None
        picks: tuple[tuple[NextWhy, tuple[Candidate, str] | None], ...] = (
            ("personal", self.personal_deed(ok)),
            ("team", self.team_deed(ok)),
        )
        for why, pick in picks:
            if pick is not None:
                return NextDeed(pick[0].scenario, why)
        if self.artifact_mode():
            return NextDeed(ok[0].scenario, "artifact")
        if (focus := self.focus_deed(ok)) is not None:
            return NextDeed(focus[0].scenario, "focus")
        return NextDeed(self.best_deed(ok)[0].scenario, "best")

    def steps(self) -> tuple[Step, ...]:
        return (
            self.metro_resume,
            self.levelup,
            self.artifact_start,
            self.bulls,
            self.battle_target,
            self.battle_stamina,
            self.stocks_dump,
            self.factory,
            self.factory_report,
            # Выбор задания занимает секунды, а ночной сон отодвинул бы его на утро.
            self.daily,
            self.lottery,
            self.sleep,
            self.book,
            self.fastfood,
            self.card,
            self.containers,
            self.prizebox,
            self.tangerine_gifts,
            self.gorbushka,
            self.tangerine,
            self.smoothie,
            self.metro,
            self.artifact_refresh,
            self.trip,
            self.startup,
            self.gadget_wear_set,
            self.gadget_buy,
            self.deeds,
            # Заточка — после дел: идёт, пока персонаж занят делом или дел нет.
            self.gadget_upgrade,
        )

    def levelup(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("levelup") or self.value("levelup_pending") is not True:
            return None
        if busy is not None:
            self.reject("levelup", {}, "busy")
            return None
        return self.act("levelup", {}, "levelup_pending")

    def artifact_start(self, busy: BusyState | None) -> Decision | None:
        """Запуск сбора, о котором попросил пользователь: только свободным персонажем (не дело, не
        сон — во сне шаги не решаются, — не метро)."""
        run = self.cfg.artifact_run
        if run.status != "starting" or run.artifact is None:
            return None
        params = {"artifact": run.artifact}
        if busy is not None:
            self.reject("artifact_start", params, "busy")
            return None
        if self.metro_inside() is not None:
            self.reject("artifact_start", params, "in_metro")
            return None
        return self.act("artifact_start", params, "artifact_starting")

    def artifact_refresh(self, busy: BusyState | None) -> Decision | None:
        """Сбор идёт: экран артефактов — после старта и раз в 3 часа; к концу сбора цикл
        просыпается (его закроет `ArtifactRuns.tick`; приостановленный — на ближайшем шаге)."""
        run = self.cfg.artifact_run
        if run.status == "active" and run.ends_at is not None:
            self.wake(run.ends_at, "artifact_end")
        if not self.artifact_mode():
            return None
        seen = self.s.artifact_collect
        since = run.started_at
        if seen is not None and seen.src != "doubtful" and (since is None or seen.at >= since):
            due = seen.at + ARTIFACT_REREAD
            if self.now < due:
                self.wake(due, "refresh", "artifacts")
                return None
        return self.refresh("artifact", "artifact_collect")

    def sleep(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("sleep"):
            return None
        if (field := self.stale_of("sleep_deadline")) is not None:
            return self.refresh("sleep", field)
        deadline: datetime | None = self.value("sleep_deadline")
        if deadline is None:
            return None
        start, forced = self.sleep_plan(deadline)
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
        return self.act("sleep", params, "sleep_deadline" if forced else "sleep_night")

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

    def tangerine_gifts(self, busy: BusyState | None) -> Decision | None:
        """Подарки за 🍊: купить на все 🍊 и открыть. 🔥 не тратят; покупку игра даёт и занятому
        (`open` = False — только она), открытие — только свободному."""
        if not self.feature_on("tangerine_gifts"):
            return None
        if (field := self.stale_of("tangerines", "tangerine_gifts")) is not None:
            return self.refresh("tangerine_gifts", field)
        buy = self.value("tangerines") >= TANGERINE_GIFT_PRICE
        if not buy and self.value("tangerine_gifts") <= 0:
            return None
        if busy is not None and not buy:
            self.reject("tangerine_gifts", {}, "busy")
            return None
        params = {} if busy is None else {"open": False}
        return self.act("tangerine_gifts", params, "tangerines" if buy else "have")

    # --- платное

    def gorbushka_timer(self, at: datetime | None) -> datetime | None:
        obs = self.s.gorbushka
        if at is None or obs is None or self.due(at, obs.at):
            return None
        return at

    def gorbushka(self, busy: BusyState | None) -> Decision | None:
        name = "gorbushka"
        if self.artifact_reject(name):
            return None
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

    def startup(self, busy: BusyState | None) -> Decision | None:
        """«Пилить» стартап: выше обычных дел, ниже метро и поездок. Уровень стартапа неизвестен —
        перечитать экран; потолок игры — ничего (флаг выключит цикл); персонаж ниже 18🎚 —
        отказ без /dos."""
        if not self.cfg.features.startup or busy is not None:
            return None
        if self.artifact_blocks("deed:startup"):
            self.reject("deed:startup", {}, "artifact_run")
            return None
        # Раздел закрыт по уровню персонажа: отказ без чтения экрана стартапов.
        char_level = self.value("level")
        if char_level is not None and char_level < STARTUP_MIN_LEVEL:
            self.reject("deed:startup", {}, "startup_locked")
            return None
        if (
            field := self.stale_of("startup", "motivation", "knowledge", "raw", "battle_at")
        ) is not None:
            return self.refresh("startup", field)
        seen = self.s.startup
        state = seen.value if seen is not None else None
        if state is None or state.level is None:
            return self.refresh("startup", "startup")
        if state.max:
            return None
        price = self.price("startup")
        raw_needed = STARTUP_RAW_LOW if state.level < STARTUP_RAW_LEVEL else STARTUP_RAW_FALLBACK
        end = self.now + self.duration("startup", price)
        verdict = self.window_verdict(end)
        have = self.value("motivation")
        if verdict is None:
            if have - self.motivation_reserve() - self.metro_reserve() < price.motivation:
                alone = (
                    have >= price.motivation
                    and self.value("knowledge") >= STARTUP_KNOWLEDGE
                    and self.value("raw") >= raw_needed
                    and not self.gated("deed:startup")
                )
                verdict = "reserved" if alone else "no_motivation"
                self.wake(self.value("motivation_next_at"), "motivation")
            elif self.value("knowledge") < STARTUP_KNOWLEDGE:
                verdict = "no_knowledge"
            elif self.value("raw") < raw_needed:
                verdict = "no_raw"
            else:
                verdict = self.gate("deed:startup")
        if verdict is not None:
            self.reject("deed:startup", {}, verdict)
            return None
        return self.act("deed:startup", {}, f"startup level {state.level}")

    def deeds(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("deed:") or busy is not None:
            return None
        if (field := self.stale_of("motivation", "money", "details", "battle_at")) is not None:
            return self.refresh("deeds", field)
        ok = self.doable_deeds()
        if not ok:
            return None
        if self.exit_unconfirmed:
            for candidate in ok:
                self.reject(candidate.scenario, candidate.params, "metro_stuck")
            return None
        chosen, reason = self.personal_deed(ok) or self.team_deed(ok) or self.main_deed(ok)
        for candidate in ok:
            verdict = "chosen" if candidate is chosen else "ok"
            self.candidates.append(replace(candidate, verdict=verdict))
        return Act(chosen.scenario, {}, reason, tuple(self.candidates))

    def doable_deeds(self) -> list[Candidate]:
        """Разрешённые дела с вердиктом `ok`; отказанные сразу уходят в кандидаты."""
        have: int = self.value("motivation")
        motivation = have - self.motivation_reserve() - self.metro_reserve()
        money = self.value("money") - self.ticket_reserve() - self.hotel_reserve()
        details: int = self.value("details")
        focus = self.cfg.strategy.focus
        ok: list[Candidate] = []
        mode = self.artifact_mode()
        activities: tuple[str, ...] = tuple(self.cfg.strategy.deeds)
        if mode:
            for other in activities:
                if other not in self.artifact_deeds():
                    self.reject(f"deed:{other}", {}, "artifact_run")
            activities = self.artifact_deeds()
        for activity in activities:
            name = f"deed:{activity}"
            price = self.price(activity)
            end = self.now + self.duration(activity, price)
            score = self.score(activity, price)
            params = {"today": self.done_today.get(name, 0)} if activity in focus else {}
            verdict = self.window_verdict(end)
            if verdict is None:
                if motivation < price.motivation:
                    # `reserved` — мешает только запас: без него 🔥 хватило бы, прочее пройдено.
                    alone = (
                        have >= price.motivation
                        and money >= price.money
                        and details >= price.details
                        and (score > 0 or activity in focus)
                        and not self.gated(name)
                    )
                    verdict = "reserved" if alone else "no_motivation"
                    self.wake(self.value("motivation_next_at"), "motivation")
                elif money < price.money:
                    verdict = "no_money"
                elif details < price.details:
                    verdict = "no_details"
                elif score <= 0 and activity not in focus and not mode:
                    # Важность основных дел задал пользователь, а оценка видит не весь доход:
                    # предметы крафта с добычи в статистику не входят.
                    verdict = "no_value"
                else:
                    verdict = self.gate(name)
            if verdict is None:
                ok.append(Candidate(name, params, score, "ok"))
            else:
                self.candidates.append(Candidate(name, params, score, verdict))
        return ok

    # --- поездки

    def trip(self, busy: BusyState | None) -> Decision | None:
        """Поездка: порядок `trips.vehicles` — приоритет среди готовых видов (есть на последнем
        экране «Транспорт», доступны, не кончились по сезону), на которые хватает 🔩 и 💵 сверх
        резервов. Экран перечитывается, если его нет, он старше `TRIPS_MAX_AGE` или цена готового
        вида неизвестна."""
        order = trip_vehicles(self.cfg)
        if not order or not self.feature_on("trip"):
            return None
        seen = self.s.trips
        ready: list[tuple[str, VehicleState]] = []
        if seen is None or seen.src == "doubtful":
            stale: str | None = "trips unknown"
        else:
            ready, stale = self.trip_ready(order, seen), None
            renew = seen.at + TRIPS_MAX_AGE
            if self.now >= renew:
                stale = "trips old"
            else:
                self.wake(renew, "refresh", "trips")
        if not ready and stale is None:
            return None
        params = {"vehicle": ready[0][0]} if ready else {}
        if busy is not None:
            self.reject("trip", params, "busy")
            return None
        if self.metro_inside() is not None:
            self.reject("trip", params, "in_metro")
            return None
        if stale is not None:
            return self.trips_refresh(stale, params)
        assert seen is not None
        last = seen.value.last
        if last is not None and not last.done:
            # До конца окна, в котором редьюсер примет итог прошлой поездки: после нового старта
            # опоздавший итог достался бы новой.
            result_by = last.started_at + TRIP_SPAN + TRIP_RESULT_GRACE
            if self.now <= result_by:
                self.reject("trip", params, "trip_pending")
                self.wake(result_by, "trip_result")
                return None
        deeds = ("motivation", "details") if self.feature_on("deed:") else ()
        if (field := self.stale_of("raw", "money", "battle_at", *deeds)) is not None:
            return self.refresh("trip", field)
        raw: int = self.value("raw")
        money = self.value("money") - self.ticket_reserve() - self.hotel_reserve()
        chosen: str | None = None
        for key, vehicle in ready:
            params = {"vehicle": key}
            if vehicle.raw is None:
                return self.trips_refresh(f"{key} price unknown", params)
            if raw < vehicle.raw:
                self.reject("trip", params, "no_raw")
            elif vehicle.money and money < vehicle.money:
                self.reject("trip", params, "no_money")
            else:
                chosen = key
                break
        if chosen is None:
            return None
        params = {"vehicle": chosen}
        verdict = self.window_verdict(self.now + TRIP_SPAN)
        if verdict is None and self.motivation_capped() and self.deed_waits():
            verdict = "motivation_cap"
        if verdict is not None:
            self.reject("trip", params, verdict)
            return None
        return self.act("trip", params, f"{chosen} ready")

    def trip_ready(
        self, order: Sequence[str], seen: Obs[TripsState]
    ) -> list[tuple[str, VehicleState]]:
        """Готовые виды по порядку; к готовности остальных — таймеры. Готовность позже экрана
        (старт + кулдаун, отказ, «Через» с округлением вниз) — с минутой запаса: на кулдауне
        сценарий поездки кнопку не нажмёт, а экран сам перечитает."""
        today = to_msk(self.now).date()
        ready: list[tuple[str, VehicleState]] = []
        for key in order:
            vehicle = seen.value.vehicles.get(key)
            if vehicle is None or not vehicle.available or vehicle.ready_at is None:
                continue
            if vehicle.expires_on is not None and vehicle.expires_on < today:
                continue
            if vehicle.ready_at > seen.at:
                at = vehicle.ready_at + READY_SLACK
                if not self.due(at):
                    self.wake(at, "trip_ready", key)
                    continue
            ready.append((key, vehicle))
        return ready

    def trips_refresh(self, reason: str, params: dict[str, Any]) -> Act | None:
        """Экран «Транспорт» — не чаще `engine.refresh_min_interval_s`."""
        self.reject("trip", params, "stale:trips")
        last = self.last_refresh.get("trips")
        if last is not None and self.now - last < self.refresh_every:
            self.reject("trips_refresh", {}, "rate_limited")
            self.wake(last + self.refresh_every, "refresh", "trips")
            return None
        return self.act("trips_refresh", {}, reason)

    def motivation_capped(self) -> bool:
        """🔥 у максимума или дойдёт до него тиком за время поездки: прирост пропал бы."""
        top: int | None = self.value("motivation_max")
        have: int | None = self.value("motivation")
        if top is None or have is None:
            return False
        tick: datetime | None = self.value("motivation_next_at")
        soon = tick is not None and tick < self.now + TRIP_SPAN
        return have >= top or (soon and have + 1 >= top)

    def deed_waits(self) -> bool:
        """Шаг дел сейчас взял бы дело на 🔥. Считается на копии планировщика: её отказы и
        таймеры — забота самого шага дел."""
        if not self.feature_on("deed:"):
            return False
        probe = _Planner(
            self.s,
            self.cfg,
            self.now,
            self.certified,
            self.last_refresh,
            self.cooldowns,
            self.last_done,
            self.metro_durations,
            self.done_today,
        )
        probe.stale = self.stale
        return any(
            probe.price(c.scenario.removeprefix("deed:")).motivation > 0
            for c in probe.doable_deeds()
        )

    def main_deed(self, ok: list[Candidate]) -> tuple[Candidate, str]:
        """Без заданий дня: в сборе — первое выполнимое по тактике, иначе основное по очереди
        или лучшее по оценке."""
        if self.artifact_mode():
            return ok[0], f"artifact {self.cfg.artifact_run.artifact}"
        return self.focus_deed(ok) or self.best_deed(ok)

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
    metro_probes: Sequence[str] = (),
) -> Decision:
    """Следующий шаг: сценарий или ожидание. `certified=None` — без ограничения (dry_run).

    `last_done` — момент последнего успешного запуска каждого сценария (журнал запусков);
    `metro_durations` — длительности прошлых забегов метро в секундах (бюджет по p90);
    `done_today` — успешные запуски дел за текущий день MSK (чередование основных дел);
    `metro_probes` — проверки выхода из метро после итога забега, которого выход не подтвердил.
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
        metro_probes,
    )
    return planner.decide()


def resume_metro(
    state: CharacterState,
    settings: Settings,
    now: datetime,
    *,
    certified: frozenset[str] | None = None,
    cooldowns: Mapping[str, datetime] | None = None,
    last_done: Mapping[str, datetime] | None = None,
    metro_durations: Sequence[float] = (),
    metro_probes: Sequence[str] = (),
) -> Act | None:
    """Только метро — решение под блоком трат до сверки: проверка выхода после итога и
    продолжение забега. Ходы метро и `/main` ничего не тратят, а сверка ждёт конца забега и
    подтверждения выхода."""
    planner = _Planner(
        state,
        settings,
        now,
        certified,
        {},
        cooldowns or {},
        last_done or {},
        metro_durations,
        None,
        metro_probes,
    )
    decision = planner.metro_probe() or planner.metro_resume(None)
    return decision if isinstance(decision, Act) else None


def outlook(
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
    metro_probes: Sequence[str] = (),
) -> Outlook:
    """«План бота» на тех же входах, что `decide`: его решение и то, что за ним. В цикле не
    используется."""

    def planner() -> _Planner:
        return _Planner(
            state,
            settings,
            now,
            certified,
            last_refresh or {},
            cooldowns or {},
            last_done or {},
            metro_durations,
            done_today,
            metro_probes,
        )

    return planner().outlook(planner)


def run_key(act: Act) -> str:
    """Один сценарий — одна запись; у рефреша — своя на источник (как ключ кулдауна)."""
    if act.scenario == "refresh":
        return f"refresh:{act.params['source']}"
    return act.scenario


def earliest(wakeups: Iterable[Wakeup]) -> tuple[Wakeup, ...]:
    """Таймеры без дублей по `(kind, key)` — самый ранний из них, по времени."""
    first: dict[tuple[str, str | None], Wakeup] = {}
    for w in wakeups:
        known = first.get((w.kind, w.key))
        if known is None or w.at < known.at:
            first[(w.kind, w.key)] = w
    return tuple(sorted(first.values(), key=lambda w: (w.at, w.reason)))


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
