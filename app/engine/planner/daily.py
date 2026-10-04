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
# Продаванов в день, если экран билета ещё не видели (на экране — «…продаванов: 4»).
GORBUSHKA_DAILY = 4
# Командное задание закрывают и другие игроки, а сообщения о его выполнении нет: прогресс
# перечитывается с экрана, пока цель не достигнута. Невыбранное глава может выбрать позже, а строки
# прогресса приходят только в итогах дел по его условию.
TEAM_REREAD = timedelta(minutes=30)
HARD = "hard"
# 🔥 командного варианта, доход дел которого неизвестен: причина `team <тип> ?🔥`.
UNKNOWN_FIRE = "?🔥"


def team_reason(kind: str, fire: int | None) -> str:
    return f"team {kind} " + (UNKNOWN_FIRE if fire is None else f"{fire}🔥")


class DailyTasks(Obligations):
    """Ежедневные задания: шаг `daily` (экран и выбор личного) и приоритет дел заданий."""

    def tasks_today(self) -> date:
        return tasks_day(self.now)

    def personal_today(self) -> PersonalTask | None:
        task: PersonalTask | None = self.value("daily_personal")
        if self.teamless():
            return None
        return task if task is not None and task.day == self.tasks_today() else None

    def team_today(self) -> TeamTask | None:
        task: TeamTask | None = self.value("team_task")
        if self.teamless():
            return None
        return task if task is not None and task.day == self.tasks_today() else None

    def midnight(self) -> datetime:
        return day_start(self.tasks_today() + timedelta(days=1))

    # --- шаг daily

    def daily(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("daily_pick") or self.metro_inside() is not None:
            return None
        if self.teamless():
            # Экран заданий открывается только из меню команды.
            self.reject("daily_refresh", {}, "no_team")
            return None
        if self.teamless_before():
            return self.refresh("daily_refresh", "team_tag")
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
        # Командное (выбирает только глава) — раньше личного.
        if team.status == "offers" and self.feature_on("team_pick"):
            if (act := self.pick_team(team.offers)) is not None:
                return act
        if personal.status != "offers":
            return None
        pick = self.pick_personal(personal.offers)
        if pick is None:
            filtered = self.artifact_mode() and any(o.level == HARD for o in personal.offers)
            self.reject("daily_pick", {}, "artifact_run" if filtered else "no_hard_offer")
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
        if team.status in ("none", "offers"):
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
        if self.artifact_mode():
            # В сборе — только задания, которые закрываются делами тактики; Горбушка — никогда.
            allowed = set(self.artifact_deeds())
            fits = [
                o
                for o in hard
                if o.type != "robPro" and allowed & set(PERSONAL_DEEDS.get(o.type, ()))
            ]
            for offer in hard:
                if offer not in fits:
                    task = f"{offer.type}_{offer.level}"
                    self.reject("daily_pick", {"task": task}, "artifact_run")
            hard = fits
        if not hard:
            return None
        feasible = [o for o in hard if self.personal_feasible(o.type, o.goal)]
        pick = (feasible or hard)[0]
        for offer in hard:
            if offer is not pick:
                verdict = "ok" if offer in feasible else "not_feasible"
                self.reject("daily_pick", {"task": f"{offer.type}_{offer.level}"}, verdict)
        return pick, bool(feasible)

    def pick_team(self, offers: tuple[TaskOfferState, ...]) -> Decision | None:
        """Командный вариант главы: только hard, с наименьшей «голой» мотивацией — будто всё
        делает один глава, без кулдаунов, срока, денег, разрешённых дел и выносливости; при
        равенстве — больше 🏆, затем порядок на экране. Ни у одного hard доход неизвестен —
        первый hard по порядку экрана (причина `?🔥`, цикл уведомит)."""
        hard = [o for o in offers if o.level == HARD]
        if not hard:
            self.reject("team_pick", {}, "no_hard_team_offer")
            return None
        fire = [self.team_motivation(o.type, o.goal) for o in hard]
        known = [i for i, f in enumerate(fire) if f is not None]
        best = min(known, key=lambda i: (fire[i], -hard[i].trophies, i)) if known else 0
        for i, offer in enumerate(hard):
            if i != best:
                task = f"{offer.type}_{offer.level}"
                self.reject("team_pick", {"task": task}, team_reason(offer.type, fire[i]))
        pick = hard[best]
        params = {"task": f"{pick.type}_{pick.level}"}
        return self.act("team_pick", params, team_reason(pick.type, fire[best]))

    def team_motivation(self, kind: str, goal: int) -> int | None:
        """🔥 на командное задание силами одного главы: `ceil(цель / доход) × 🔥 за запуск` по
        самому дешёвому делу типа; None — доход ни одного дела неизвестен."""
        costs = [
            math.ceil(goal / income) * self.price(deed).motivation
            for deed in PERSONAL_DEEDS.get(kind, ())
            if goal > 0 and (income := self.task_income(kind, deed)) > 0
        ]
        return min(costs) if costs else None

    def task_income(self, kind: str, deed: str) -> float:
        """Доход задания за запуск дела: метрика итога (`activity_stats`, иначе
        `DEED_PRIORS`); у переработки — выложенные ⚙️ из цены. 0 — неизвестен."""
        if kind == "convDets":
            return self.price(deed).details or DCONV_DETAILS
        stat = self.s.activity_stats.get(deed) or DEED_PRIORS.get(deed) or ActivityStat()
        return float(getattr(stat, TASK_METRIC[kind])) if kind in TASK_METRIC else 0.0

    def personal_feasible(self, kind: str, goal: int) -> bool:
        if kind == "robPro":
            # ⚙️ за победу — среднее по боям персонажа (с ⚫️VIP-сетом больше), до них — 12.
            stat = self.s.activity_stats.get("gorbushka") or DEED_PRIORS["gorbushka"]
            return self.feature_on("gorbushka") and self.fights_today() * stat.details >= goal
        return any(
            self.deed_allowed(deed) and self.fits_today(kind, goal, deed)
            for deed in PERSONAL_DEEDS.get(kind, ())
        )

    def deed_allowed(self, deed: str) -> bool:
        name = f"deed:{deed}"
        allowed = self.artifact_deeds() if self.artifact_mode() else self.cfg.strategy.deeds
        if deed not in allowed or not self.feature_on(name):
            return False
        return self.certified is None or name in self.certified

    def fits_today(self, kind: str, goal: int, deed: str) -> bool:
        """Грубая оценка «успеет ли до 24:00»: 🔥 с приростом, время без сна и окна битвы, $
        сверх резервов и ⚙️ на переработку."""
        price = self.price(deed)
        income = self.task_income(kind, deed)
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
        """Сколько боёв Горбушки ещё успеет пройти до крайнего срока (раз в час без сна): остаток
        текущего билета, пока он жив, и новый билет — сейчас, если билета нет, а иначе с возврата
        (все одолены — «приходи через …», идут бои — конец билета), если билет по карману сверх
        резервов. Состояние — последнее известное, даже старше 6 часов: его меняют только бои и
        покупка билета, которые бот видит, а прошедшие с экрана возврат и конец билета означают
        новый билет сейчас."""
        seen = self.s.gorbushka
        if seen is None or seen.src == "doubtful":
            return 0
        g = seen.value
        deadline = self.task_deadline()
        fights = 0
        renew: datetime | None = None
        if g.state == "need_ticket":
            renew = self.now
        elif g.state == "done":
            renew = g.comeback_at
        elif g.state in ("meeting", "waiting") and g.won is not None and g.total is not None:
            first = max(self.now, g.next_fight_at or self.now)
            end = min(deadline, g.ticket_until) if g.ticket_until else deadline
            fights = max(0, min(g.total - g.won, self.fight_slots(first, end)))
            renew = g.ticket_until
        if renew is not None and self.ticket_affordable():
            slots = self.fight_slots(max(self.now, renew), deadline)
            fights += min(g.total or GORBUSHKA_DAILY, slots)
        return fights

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
