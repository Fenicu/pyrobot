from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from app.engine.gametime import day_start, tasks_day
from app.engine.parsing.daily import PERSONAL_DEEDS
from app.engine.planner.base import BATTLE_AFTER, BATTLE_BEFORE
from app.engine.planner.obligations import Obligations
from app.engine.planner.types import Candidate, Decision
from app.engine.state.model import (
    DEED_PRIORS,
    ActivityStat,
    BusyState,
    PersonalTask,
    TaskOfferState,
    TeamTask,
)
from app.engine.state.reducer import GORBUSHKA_FIGHT_GAP

DAILY_REFRESH_EVERY = timedelta(minutes=10)
# Вокруг сброса в 00:00 MSK задание не выбирается: /t_… мог бы дойти уже до нового дня.
MIDNIGHT_GUARD = timedelta(minutes=2)
MOTIVATION_REGEN = timedelta(hours=1)
# Доход личного задания за запуск дела: метрика итога; у переработки — выложенные ⚙️.
TASK_METRIC = {
    "jobMoney": "money",
    "walkMoney": "money",
    "learnKnows": "knowledge",
    "confKnows": "knowledge",
    "materials": "raw",
}
DCONV_DETAILS = 10
ROB_DETAILS = 12
# Продаванов в день, если экран билета ещё не видели (на экране — «…продаванов: 4»).
GORBUSHKA_DAILY = 4
# Командное задание закрывают и другие игроки, а сообщения о его выполнении нет: прогресс
# перечитывается с экрана, пока цель не достигнута. Невыбранное глава может выбрать позже, а строки
# прогресса приходят только в итогах дел по его условию.
TEAM_REREAD = timedelta(minutes=30)
HARD = "hard"


class DailyTasks(Obligations):
    """Ежедневные задания: шаг `daily` (экран и выбор личного) и приоритет дел заданий."""

    def tasks_today(self) -> date:
        return tasks_day(self.now)

    def personal_today(self) -> PersonalTask | None:
        task: PersonalTask | None = self.value("daily_personal")
        return task if task is not None and task.day == self.tasks_today() else None

    def team_today(self) -> TeamTask | None:
        task: TeamTask | None = self.value("team_task")
        return task if task is not None and task.day == self.tasks_today() else None

    def midnight(self) -> datetime:
        return day_start(self.tasks_today() + timedelta(days=1))

    # --- шаг daily

    def daily(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("daily_pick") or self.metro_inside() is not None:
            return None
        start, midnight = day_start(self.tasks_today()), self.midnight()
        if self.now < start + MIDNIGHT_GUARD:
            self.wake(start + MIDNIGHT_GUARD, "daily_midnight")
            return None
        if self.now >= midnight - MIDNIGHT_GUARD:
            self.wake(midnight + MIDNIGHT_GUARD, "daily_midnight")
            return None
        # После сброса задания новые: экран перечитывается.
        self.wake(midnight + MIDNIGHT_GUARD, "daily_reset")
        personal, team = self.personal_today(), self.team_today()
        if personal is None or team is None:
            return self.daily_refresh("tasks unknown")
        reread = "team deeds unknown" if self.team_deeds_unknown(team) else self.team_stale(team)
        # Перечитать не дал лимит — выбор личного задания от этого не откладывается.
        if reread is not None and (act := self.daily_refresh(reread)) is not None:
            return act
        if personal.status != "offers":
            return None
        pick = self.pick_personal(personal.offers)
        if pick is None:
            self.reject("daily_pick", {}, "no_hard_offer")
            return None
        offer, feasible = pick
        reason = f"personal {offer.type}" + ("" if feasible else " (not feasible)")
        return self.act("daily_pick", {"task": f"{offer.type}_{offer.level}"}, reason)

    def team_deeds_unknown(self, team: TeamTask) -> bool:
        # Дела неизвестны у задания из строки прогресса; у экранного пустая подсказка — это
        # знание, и перечитывать экран каждые 10 минут бесполезно.
        seen = self.s.team_task
        derived = seen is not None and seen.src == "derived"
        return team.status == "active" and not team.activities and derived

    def team_stale(self, team: TeamTask) -> str | None:
        seen = self.s.team_task
        if seen is None or self.now - seen.at < TEAM_REREAD:
            return None
        if team.status == "none":
            return "team not chosen"
        if team.status == "active" and team.current < team.goal:
            return "team progress stale"
        return None

    def daily_refresh(self, reason: str) -> Decision | None:
        last = self.last_refresh.get("daily")
        # Первое чтение нового дня лимит не откладывает.
        fresh = last is not None and tasks_day(last) == self.tasks_today()
        if last is not None and fresh and self.now - last < DAILY_REFRESH_EVERY:
            self.reject("daily_refresh", {}, "rate_limited")
            self.wake(last + DAILY_REFRESH_EVERY, "refresh", "daily")
            return None
        return self.act("daily_refresh", {}, reason)

    def pick_personal(
        self, offers: tuple[TaskOfferState, ...]
    ) -> tuple[TaskOfferState, bool] | None:
        """Только hard (у всех по 90🏆), среди них — первое по `daily.personal_order`;
        выполнимые до 24:00 — вперёд, но и невыполнимое лучше, чем никакого. Второе значение —
        выполнимо ли выбранное."""
        order = self.cfg.daily.personal_order
        rank: dict[str, int] = {kind: i for i, kind in enumerate(order)}
        hard = sorted(
            (o for o in offers if o.level == HARD), key=lambda o: rank.get(o.type, len(order))
        )
        if not hard:
            return None
        feasible = [o for o in hard if self.personal_feasible(o.type, o.goal)]
        pick = (feasible or hard)[0]
        for offer in hard:
            if offer is not pick:
                verdict = "ok" if offer in feasible else "not_feasible"
                self.reject("daily_pick", {"task": f"{offer.type}_{offer.level}"}, verdict)
        return pick, bool(feasible)

    def personal_feasible(self, kind: str, goal: int) -> bool:
        if kind == "robPro":
            return self.feature_on("gorbushka") and self.fights_today() * ROB_DETAILS >= goal
        return any(
            self.deed_allowed(deed) and self.fits_today(kind, goal, deed)
            for deed in PERSONAL_DEEDS.get(kind, ())
        )

    def deed_allowed(self, deed: str) -> bool:
        name = f"deed:{deed}"
        if deed not in self.cfg.strategy.deeds or not self.feature_on(name):
            return False
        return self.certified is None or name in self.certified

    def fits_today(self, kind: str, goal: int, deed: str) -> bool:
        """Грубая оценка «успеет ли до 24:00»: 🔥 с приростом, время без сна и окна битвы, $
        сверх резервов и ⚙️ на переработку."""
        price = self.price(deed)
        stat = self.s.activity_stats.get(deed) or DEED_PRIORS.get(deed) or ActivityStat()
        if kind == "convDets":
            income: float = price.details or DCONV_DETAILS
        else:
            income = getattr(stat, TASK_METRIC[kind]) if kind in TASK_METRIC else 0.0
        if income <= 0:
            return False
        runs = math.ceil(goal / income)
        deadline = self.task_deadline()
        regen = int((deadline - self.now) / MOTIVATION_REGEN)
        motivation = int(self.value("motivation") or 0) + regen
        left = self.awake_between(self.now, deadline)
        battle = self.battle_time()
        if battle is not None and self.now < battle < deadline:
            left -= BATTLE_BEFORE + BATTLE_AFTER
        money = int(self.value("money") or 0) - self.ticket_reserve() - self.hotel_reserve()
        details = int(self.value("details") or 0)
        # Резервы больше денег — не повод отказываться от дела, которое денег не стоит.
        return (
            runs * price.motivation <= motivation
            and runs * self.duration(deed, price) <= left
            and runs * price.money <= max(money, 0)
            and runs * price.details <= max(details, 0)
        )

    def task_deadline(self) -> datetime:
        """Крайний срок заданий: 24:00 или начало сна, который длится за полночь."""
        sleep = self.sleep_today()
        if sleep is None or sleep[1] <= self.midnight():
            return self.midnight()
        return sleep[0]

    def sleep_today(self) -> tuple[datetime, datetime] | None:
        """Ближайший сон, если он начнётся до 24:00."""
        deadline: datetime | None = self.value("sleep_deadline")
        if not self.sleep_runs() or deadline is None:
            return None
        start = max(self.now, self.sleep_start(deadline))
        if start >= self.midnight():
            return None
        return start, start + timedelta(hours=self.cfg.sleep.duration_h)

    def awake_between(self, start: datetime, end: datetime) -> timedelta:
        """Время от `start` до `end` без сна: во сне ни дела, ни бои не идут."""
        left = end - start
        if (sleep := self.sleep_today()) is not None:
            left -= max(min(end, sleep[1]) - max(start, sleep[0]), timedelta(0))
        return left

    def fights_today(self) -> int:
        """Сколько боёв Горбушки ещё успеет пройти до крайнего срока (раз в час без сна, пока жив
        билет); билета нет — сколько даст новый, если он по карману сверх резервов."""
        g = self.gorbushka_state()
        deadline = self.task_deadline()
        if g is None:
            return 0
        if g.state == "need_ticket":
            if not self.ticket_affordable():
                return 0
            return min(g.total or GORBUSHKA_DAILY, self.fight_slots(self.now, deadline))
        if g.state not in ("meeting", "waiting") or g.won is None or g.total is None:
            return 0
        first = max(self.now, g.next_fight_at or self.now)
        end = min(deadline, g.ticket_until) if g.ticket_until else deadline
        return max(0, min(g.total - g.won, self.fight_slots(first, end)))

    def fight_slots(self, first: datetime, end: datetime) -> int:
        """Бои в `first` и дальше раз в час вне сна, пока не наступил `end`: бой в сам срок —
        уже поздно."""
        awake = self.awake_between(first, end)
        return math.ceil(awake / GORBUSHKA_FIGHT_GAP) if awake > timedelta(0) else 0

    # --- приоритет дел заданий

    def personal_deed(self, ok: list[Candidate]) -> tuple[Candidate, str] | None:
        task = self.personal_today()
        if task is None or task.status != "active" or task.chosen is None:
            return None
        if task.chosen.goal and task.current >= task.chosen.goal:
            return None
        best = _best_of(ok, task.chosen.activities)
        return None if best is None else (best, f"personal {task.chosen.type or 'unknown'}")

    def team_deed(self, ok: list[Candidate]) -> tuple[Candidate, str] | None:
        task = self.team_today()
        if task is None or task.status != "active" or task.current >= task.goal:
            return None
        best = _best_of(ok, task.activities)
        if best is None:
            return None
        deed = best.scenario.removeprefix("deed:")
        return best, f"team {deed} {task.current}/{task.goal}"


def _best_of(ok: list[Candidate], activities: tuple[str, ...]) -> Candidate | None:
    # Горбушка — не дело: задание на неё порядок дел не меняет.
    names = {f"deed:{activity}" for activity in activities}
    ready = [c for c in ok if c.scenario in names]
    return max(ready, key=lambda c: c.score or 0.0) if ready else None
