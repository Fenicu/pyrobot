from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from app.engine.gametime import MSK, day_start, tasks_day, to_msk
from app.engine.market import pick_stock
from app.engine.metro.budget import GAME_KICK
from app.engine.metro.solver import policy_of
from app.engine.parsing.metro import ENTRY_COST, METRO_COOLDOWN
from app.engine.parsing.smoothie import recipe_need
from app.engine.planner.base import (
    BATTLE_AFTER,
    BATTLE_BEFORE,
    READY_SLACK,
    PlannerBase,
    battle_hour,
)
from app.engine.planner.types import Decision, Reserve
from app.engine.state.model import BusyState, LotteryState, MetroRunRef, StockLimits
from app.engine.state.reducer import LOTTERY_CURRENCIES

# Деньги на отель дела не тратят за столько до начала сна.
HOTEL_RESERVE_AHEAD = timedelta(hours=3)
# Сон ждёт конца записи на фабрику, только если после неё до дедлайна остаётся запас.
AFTER_FACTORY_MARGIN = timedelta(minutes=15)
TARGET_LAST_CALL = timedelta(minutes=1)
STAMINA_AHEAD = timedelta(minutes=30)
FASTFOOD_BEFORE = timedelta(minutes=2)
DUMP_SPAN = timedelta(minutes=10)
BULLS_INVITE_TTL = timedelta(minutes=3)
NOT_PLAYER_PAUSE = timedelta(hours=24)
FACTORY_OPEN, FACTORY_CLOSE, FACTORY_BATTLE = time(18, 0), time(18, 15), time(18, 30)
# Личный отчёт о битве (/fb) — после её конца; позже 23:59 за сегодняшний не пытаться.
FACTORY_REPORT_FROM, FACTORY_REPORT_UNTIL = time(18, 31), time(23, 59)
NIGHT_START, NIGHT_END = time(22, 0), time(8, 0)
SLEEP_NIGHT_OPEN, SLEEP_AFTER_BULLS, SLEEP_WAKE_BY = time(22, 5), time(0, 30), time(12, 45)
SMOOTHIE_RESET = time(3, 0)
# Тираж стартует в 19:17, продажа закрыта с 21:07; сценарий стартует не позже 21:05.
LOTTERY_OPEN, LOTTERY_LAST_START = time(19, 17), time(21, 5)
METRO_SAFETY = 1.5
# Забег, прерванный рестартом, продолжается, если последний его экран свежий и игра ещё не
# выкинула персонажа.
METRO_STALE = timedelta(hours=2)


def p90(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)]


def msk_at(moment: datetime, at: time, days: int = 0) -> datetime:
    """Момент `at` по Москве в день `moment` (со сдвигом `days`), в UTC."""
    local = to_msk(moment)
    day = datetime(local.year, local.month, local.day, tzinfo=MSK) + timedelta(days=days)
    return day.replace(hour=at.hour, minute=at.minute)


def is_night(moment: datetime) -> bool:
    hour = to_msk(moment).time()
    return hour >= NIGHT_START or hour < NIGHT_END


def night_start(moment: datetime) -> datetime:
    """Начало ночи (22:00), к которой относится `moment`: текущей или последней прошедшей."""
    today = msk_at(moment, NIGHT_START)
    return today if moment >= today else today - timedelta(days=1)


def game_day_start(moment: datetime) -> datetime:
    reset = msk_at(moment, SMOOTHIE_RESET)
    return reset if moment >= reset else reset - timedelta(days=1)


class Obligations(PlannerBase):
    """Слой 1: жёсткие окна и обязательства — битва, фабрика, биржевики, мандарин, сон."""

    # --- битва

    def battle_target(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("battle_target"):
            return None
        # Цель можно менять и во время дела, и во сне.
        if (field := self.stale_of("battle_at")) is not None:
            return self.refresh("battle_target", field)
        battle = self.upcoming_battle()
        if battle is None or battle - self.now <= TARGET_LAST_CALL:
            return None
        desired = self.target_for(battle)
        if self.target_ready(battle, desired):
            return None
        return self.act("battle_target", {"target": desired}, "battle_target")

    def target_for(self, battle: datetime) -> str:
        """Цель на битву: своя на её час по Москве или общая."""
        return self.cfg.battle.overrides.get(to_msk(battle).hour, self.cfg.battle.target)

    def target_ready(self, battle: datetime, desired: str) -> bool:
        seen, known = self.s.battle_target, self.s.battle_at
        # Время предстоящей битвы видно только после прошлой: цель, снятая вместе с ним или
        # позже, относится к предстоящей битве.
        if seen is not None and known is not None and seen.at >= known.at:
            if seen.value is not None:
                return bool(seen.value == desired)
        # Цели в профиле нет (старые профили) — опора на выставленную цель и её битву.
        done = self.s.battle_target_set
        if done is None or done.value.target != desired:
            return False
        return battle_hour(done.value.battle_at, done.at) == battle

    def battle_stamina(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("battle_stamina"):
            return None
        battle = self.upcoming_battle()
        if battle is None or battle - self.now > STAMINA_AHEAD:
            return None
        if self.stale_of("stamina") is not None or self.value("stamina") > 0:
            return None
        if busy is not None and busy.activity == "eat":
            return None
        # Фастфуд — по тем же правилам, что и обычно (порядок видов, запас бананов); неизвестные
        # запасы сначала обновляются, а не заменяются платной едой.
        if self.feature_on("fastfood"):
            if (field := self.stale_of("food_stock", "fastfood_ready_at")) is not None:
                return self.refresh("battle_stamina", field)
            if (food := self.pick_food()) is not None:
                ready = self.timer("fastfood_ready_at")
                if ready is None:
                    return self.act("fastfood", {"food": food}, "battle_stamina")
                if ready < battle - FASTFOOD_BEFORE:
                    self.wake(ready, "fastfood_ready")
                    return None
        if not self.feature_on("deed:eat") or busy is not None:
            return None
        price = self.price("eat")
        if self.now + self.duration("eat", price) > battle - BATTLE_BEFORE:
            return None
        if self.stale_of("money") is not None or self.value("money") < price.money:
            return None
        return self.act("deed:eat", {}, "battle_stamina")

    def stocks_dump(self, busy: BusyState | None) -> Decision | None:
        if not self.feature_on("stocks_dump"):
            return None
        battle = self.upcoming_battle()
        if battle is None:
            return None
        lead = timedelta(minutes=self.cfg.stocks.dump_lead_min)
        start, end = battle - lead - DUMP_SPAN, battle - TARGET_LAST_CALL
        if self.now < start:
            self.wake(start, "stocks_dump")
            return None
        if self.now >= end:
            return None
        limits: StockLimits | None = self.value("stock_limits")
        hour = to_msk(self.now).hour
        if limits is not None and not limits.open_hour <= hour < limits.close_hour:
            self.reject("stocks_dump", {}, "market_closed")
            return None
        if (field := self.stale_of("money", "company")) is not None:
            return self.refresh("stocks_dump", field)
        own: str | None = self.value("company")
        if own is None:
            # Значок в свежем профиле не распознан: своей может оказаться любая акция.
            self.reject("stocks_dump", {}, "company_unknown")
            return None
        keep = self.cfg.stocks.cash_floor + self.ticket_reserve() + self.night_hotel()
        # После покупки игра оставляет не меньше неснижаемого остатка биржи.
        floor = max(keep, limits.reserve) if limits is not None else keep
        if self.value("money") - floor < self.cfg.stocks.min_dump:
            return None
        # Котировки меняются после каждой битвы: предпроверка — по увиденным в окне слива.
        quotes = self.s.stock_quotes
        margin = self.cfg.stocks.sell_cap_margin
        if quotes is not None and quotes.at >= start and limits is not None:
            if not pick_stock(quotes.value, limits.min_buy, limits.max_sell, margin, own):
                self.reject("stocks_dump", {}, "no_stock")
                return None
        return self.act("stocks_dump", {"keep": keep, "margin": margin}, "battle_soon")

    # --- фабрика

    def factory_pending(self) -> bool:
        """Запись на сегодняшнюю битву за фабрику ещё нужна."""
        if not self.feature_on("factory_signup") or self.teamless():
            return False
        opens, closes = msk_at(self.now, FACTORY_OPEN), msk_at(self.now, FACTORY_CLOSE)
        if self.now >= closes:
            return False
        for name in ("factory_signed", "factory_skip"):
            obs = getattr(self.s, name)
            if obs is not None and obs.value is True and obs.at >= opens:
                return False
        won: datetime | None = self.value("factory_won_at")
        # После победы команды следующую битву она пропускает.
        return won is None or won < msk_at(self.now, FACTORY_BATTLE, days=-1)

    def factory(self, busy: BusyState | None) -> Decision | None:
        if self.feature_on("factory_signup") and self.teamless():
            # Битвы за фабрику — между командами: без команды записи нет.
            self.reject("factory_signup", {}, "no_team")
            return None
        if not self.factory_pending():
            return None
        opens = msk_at(self.now, FACTORY_OPEN)
        if self.now < opens:
            self.wake(opens, "factory_open")
            return None
        if busy is not None:
            self.reject("factory_signup", {}, "busy")
            return None
        if self.teamless_before():
            return self.refresh("factory_signup", "team_tag")
        return self.act("factory_signup", {}, "factory_window")

    def factory_joined(self) -> bool:
        """Персонаж записан на сегодняшнюю битву: запуск записи или экран фабрики после начала
        записи; пропуск после победы команды — не записан."""
        opens = msk_at(self.now, FACTORY_OPEN)
        skip = self.s.factory_skip
        if skip is not None and skip.value is True and skip.at >= opens:
            return False
        signup = self.last_done.get("factory_signup")
        if signup is not None and signup >= opens:
            return True
        signed = self.s.factory_signed
        return signed is not None and signed.value is True and signed.at >= opens

    def factory_report(self, busy: BusyState | None) -> Decision | None:
        """Отчёт о сегодняшней битве за фабрику (`/fb`, навигация — и во время дела): после 18:31,
        если персонаж сегодня записан, сегодняшний отчёт ещё не получен и сегодня запуска ещё не
        было; до 23:59. Во сне шаг не решается."""
        if not self.feature_on("factory_report") or self.teamless():
            return None
        if self.now >= msk_at(self.now, FACTORY_REPORT_UNTIL) or not self.factory_joined():
            return None
        today = tasks_day(self.now)
        seen: date | None = self.value("factory_report_day")
        if seen is not None and seen >= today:
            return None
        ran = self.last_done.get("factory_report")
        if ran is not None and ran >= day_start(today):
            return None
        opens = msk_at(self.now, FACTORY_REPORT_FROM)
        if self.now < opens:
            self.wake(opens, "factory_report")
            return None
        return self.act("factory_report", {}, "factory_report")

    def blocks_factory(self, end: datetime) -> bool:
        """Дело, заканчивающееся позже открытия записи, мешает записаться вовремя."""
        return self.factory_pending() and end > msk_at(self.now, FACTORY_OPEN)

    # --- биржевики, мандарин, смузи

    def bulls(self, busy: BusyState | None) -> Decision | None:
        invite = self.s.bulls_invite
        if not self.feature_on("bulls_join") or invite is None:
            return None
        if self.now - invite.at > BULLS_INVITE_TTL or not is_night(self.now):
            return None
        won: datetime | None = self.value("bulls_won_at")
        if won is not None and won >= night_start(self.now):
            return None
        if busy is not None:
            self.reject("bulls_join", {}, "busy")
            return None
        return self.act("bulls_join", {"code": invite.value}, "bulls_invite")

    def tangerine(self, busy: BusyState | None) -> Decision | None:
        reply_to = self.cfg.chats.tangerine_reply_to
        if not self.feature_on("tangerine") or reply_to is None:
            return None
        last = self.last_done.get("tangerine")
        if last is not None:
            # Запуск стартует раньше, чем /gt реально уходит, а кулдаун игра считает от отправки.
            ready = last + timedelta(hours=self.cfg.tangerine.interval_h) + READY_SLACK
            if not self.due(ready):
                self.wake(ready, "tangerine_ready")
                return None
        if (cooldown := self.timer("tangerine_ready_at")) is not None:
            self.wake(cooldown, "tangerine_ready")
            return None
        refused = self.s.tangerine_not_player
        if refused is not None and self.now < refused.at + NOT_PLAYER_PAUSE:
            self.reject("tangerine", {}, "not_player")
            self.wake(refused.at + NOT_PLAYER_PAUSE, "tangerine_not_player")
            return None
        params = {"chat": self.cfg.chats.tangerine_chat_id, "reply_to": reply_to}
        return self.act("tangerine", params, "tangerine_ready")

    def smoothie(self, busy: BusyState | None) -> Decision | None:
        recipe = self.s.smoothie_recipe
        if not self.feature_on("smoothie") or recipe is None:
            return None
        day = game_day_start(self.now)
        if recipe.at < day:
            return None
        # Неверный рецепт тоже тратит ингредиенты: варим не больше раза за игровой день.
        cooked = self.last_done.get("smoothie")
        if cooked is not None and cooked >= day:
            return None
        bonus = self.s.smoothie_bonus
        if bonus is not None and bonus.at >= day and bonus.value is not None:
            return None
        stock = self.s.smoothie_ingredients
        if stock is not None and stock.at >= day:
            need = recipe_need(recipe.value.recipe)
            if any(stock.value.get(name, 0) < n for name, n in need.items()):
                return None
        if busy is not None:
            self.reject("smoothie", {}, "busy")
            return None
        return self.act("smoothie", {"recipe": recipe.value.recipe}, "smoothie_recipe")

    # --- лотерея

    def lottery_params(self) -> dict[str, Any]:
        cfg = self.cfg.lottery
        params: dict[str, Any] = {"reserve": self.ticket_reserve() + self.hotel_reserve()}
        for c in LOTTERY_CURRENCIES:
            params[f"tickets_{c}"] = getattr(cfg.tickets, c)
            params[f"keep_{c}"] = getattr(cfg.keep, c)
        return params

    def lottery(self, busy: BusyState | None) -> Decision | None:
        """Билеты тиража: в окне продажи, пока куплено меньше цели и хватает хотя бы на билет
        недостающей валюты; во время дела покупка идёт, во сне — нет (шаг во сне не решается)."""
        if not self.feature_on("lottery_buy"):
            return None
        opens, last = msk_at(self.now, LOTTERY_OPEN), msk_at(self.now, LOTTERY_LAST_START)
        if self.now < opens:
            self.wake(opens, "lottery_open")
            return None
        if self.now > last:
            return None
        params = self.lottery_params()
        seen = self.s.lottery
        snap = seen.value if seen is not None else None
        # Снимок до старта тиража — прошлого тиража: его данные неизвестны.
        if seen is None or seen.at < opens or seen.src == "doubtful" or not _complete(snap):
            return self.act("lottery_buy", params, "lottery_unknown")
        assert snap is not None
        missing = self.lottery_missing(snap)
        verdicts = {c: self.lottery_affordable(snap, c) for c in missing}
        ready = [c for c, v in verdicts.items() if v == "ok"]
        if ready:
            return self.act("lottery_buy", params, "lottery " + ",".join(ready))
        # Нехватка при прошлой попытке: повтор — только по новому наблюдению ресурса (профиль),
        # а не по экрану лотереи.
        if (observe := next((c for c, v in verdicts.items() if v == "observe"), None)) is not None:
            return self.refresh("lottery_buy", observe)
        if missing:
            self.reject("lottery_buy", params, "cant_afford")
        return None

    def lottery_missing(self, snap: LotteryState) -> list[str]:
        tickets = self.cfg.lottery.tickets
        missing = []
        for c in LOTTERY_CURRENCIES:
            limit = (snap.limits or {})[c]
            wanted = getattr(tickets, c)
            want = limit if wanted == "max" else min(wanted, limit)
            if (snap.bought or {})[c] < want:
                missing.append(c)
        return missing

    def lottery_affordable(
        self, snap: LotteryState, currency: str
    ) -> Literal["ok", "wait", "observe"]:
        """`ok` — хватает на билет сверх запаса или ресурс неизвестен (экран лотереи покажет);
        валюта, на которую при прошлой попытке не хватило, ждёт роста ресурса: `observe` —
        ресурс неизвестен или устарел, нужно новое наблюдение, `wait` — не вырос."""
        have: int | None = self.value(currency)
        stale = have is None or currency in self.stale
        if currency in snap.short:
            if stale:
                return "observe"
            waited = snap.short[currency]
            if waited is not None and have is not None and have <= waited:
                return "wait"
        elif stale:
            return "ok"
        assert have is not None
        keep: int = getattr(self.cfg.lottery.keep, currency)
        free = have - keep
        if currency == "money":
            free -= self.ticket_reserve() + self.hotel_reserve()
        return "ok" if free >= (snap.prices or {})[currency] else "wait"

    # --- метро

    def metro_run(self) -> timedelta:
        """Консервативная длительность забега: max(min_budget_min, p90 прошлых × 1.5)."""
        history = timedelta(seconds=p90(self.metro_durations) * METRO_SAFETY)
        return max(timedelta(minutes=self.cfg.metro.min_budget_min), history)

    def metro_margin(self) -> timedelta:
        return timedelta(minutes=self.cfg.metro.margin_min)

    def metro_ready(self) -> datetime | None:
        """Когда кулдаун метро пройдёт; None — уже прошёл или неизвестен (экран входа покажет)."""
        if (ready := self.timer("metro_ready_at")) is not None:
            return ready
        last = self.last_done.get("metro")
        if self.s.metro_ready_at is None and last is not None:
            after = last + METRO_COOLDOWN
            if not self.due(after):
                return after
        return None

    def metro_fits(self, start: datetime) -> bool:
        battle = self.upcoming_battle()
        return battle is None or battle - start >= self.metro_run() + self.metro_margin()

    def metro_hold(self) -> Reserve | None:
        """🔥 на вход, которые дела не тратят: спуск станет доступен не позже чем через
        `strategy.reserve_ahead_min.metro` минут, и битва его позволит."""
        if not self.feature_on("metro"):
            return None
        ahead = timedelta(minutes=self.cfg.strategy.reserve_ahead_min.metro)
        ready = self.metro_ready() or self.now
        if not ahead or ready - self.now > ahead or not self.metro_fits(ready):
            return None
        return Reserve("metro", ENTRY_COST, max(ready, self.now))

    def metro_reserve(self) -> int:
        reserve = self.metro_hold()
        return 0 if reserve is None else reserve.motivation

    def reserves(self) -> tuple[Reserve, ...]:
        """Запасы 🔥 от дел по времени."""
        held = (r for r in (self.gorbushka_hold(), self.metro_hold()) if r is not None)
        return tuple(sorted(held, key=lambda r: (r.at, r.kind)))

    def metro_inside(self) -> tuple[MetroRunRef, datetime] | None:
        """Забег, в котором персонаж ещё может быть, и его битва (известная на входе, к началу
        часа): игра выкидывает из метро за 15 минут до неё."""
        inside = self.s.metro_message
        if inside is None or inside.value is None or inside.value.battle_at is None:
            return None
        known = inside.value.battle_at
        battle = battle_hour(known.value, known.at)
        return (inside.value, battle) if self.now < battle - GAME_KICK else None

    def metro(self, busy: BusyState | None) -> Decision | None:
        if self.artifact_reject("metro"):
            return None
        if not self.feature_on("metro"):
            return None
        if (inside := self.metro_inside()) is not None:
            # Нового входа нет, пока персонаж в метро: забег продолжается или ждёт выброса.
            self.reject("metro", {}, "in_metro")
            self.wake(inside[1] - GAME_KICK, "metro_kick")
            return None
        if (ready := self.metro_ready()) is not None:
            self.wake(ready, "metro_ready")
            return None
        if (field := self.stale_of("battle_at")) is not None:
            return self.refresh("metro", field)
        battle = self.upcoming_battle()
        if battle is None:
            return None
        if not self.metro_fits(self.now):
            self.reject("metro", {}, "battle_window")
            self.wake(battle + BATTLE_AFTER, "battle")
            return None
        end = self.now + self.metro_run()
        deadline: datetime | None = self.value("sleep_deadline")
        if deadline is not None and end > deadline:
            self.reject("metro", {}, "sleep_deadline")
            return None
        if self.blocks_factory(end):
            self.reject("metro", {}, "factory_window")
            return None
        if busy is not None:
            self.reject("metro", {}, "busy")
            return None
        if (field := self.stale_of("motivation")) is not None:
            return self.refresh("metro", field)
        have: int = self.value("motivation")
        if have - self.motivation_reserve() < ENTRY_COST:
            alone = have >= ENTRY_COST and not self.gated("metro")
            self.reject("metro", {}, "reserved" if alone else "no_motivation")
            self.wake(self.value("motivation_next_at"), "motivation")
            return None
        return self.act("metro", self.metro_params(battle), "metro_ready")

    def metro_params(self, battle: datetime) -> dict[str, Any]:
        cfg = self.cfg.metro
        return {
            "battle_at": battle.isoformat(),
            "margin_min": cfg.margin_min,
            "buffs": list(cfg.buffs),
            **asdict(policy_of(cfg)),
        }

    def metro_resume(self, busy: BusyState | None) -> Decision | None:
        """Персонаж остался в метро (рестарт, остановка сценария): продолжить забег сразу."""
        seen = self.s.metro_message
        if not self.feature_on("metro_resume") or seen is None:
            return None
        if (inside := self.metro_inside()) is None or self.now - seen.at > METRO_STALE:
            return None
        run, battle = inside
        if seen.src == "doubtful":
            # Последний экран забега незнакомый: продолжать только после нового распознанного.
            self.reject("metro", {"resume": run.message_id}, "metro_unknown_screen")
            return None
        params = {**self.metro_params(battle), "resume": run.message_id}
        return self.act("metro", params, "metro_resume")

    # --- сон

    def sleep_start(self, deadline: datetime) -> datetime:
        """Начало сна: ночное окно без битв, но не позже, чем требует дедлайн."""
        forced = deadline - timedelta(minutes=self.cfg.sleep.lead_min)
        allowed: datetime | None = self.value("sleep_allowed_at")
        earliest = max(self.now, allowed) if allowed is not None else self.now
        return self.after_factory(min(forced, self.night_slot(earliest)), deadline)

    def after_factory(self, start: datetime, deadline: datetime) -> datetime:
        """Сон, накрывающий запись на фабрику, ждёт её конца, если дедлайн это позволяет."""
        opens, closes = msk_at(self.now, FACTORY_OPEN), msk_at(self.now, FACTORY_CLOSE)
        duration = timedelta(hours=self.cfg.sleep.duration_h)
        if not self.factory_pending() or start >= closes or start + duration <= opens:
            return start
        return closes if closes + AFTER_FACTORY_MARGIN <= deadline else start

    def night_slot(self, earliest: datetime) -> datetime:
        duration = timedelta(hours=self.cfg.sleep.duration_h)
        local = to_msk(earliest)
        # Ночь начинается вечером базового дня; до полудня — это ночь прошлого вечера.
        evening = msk_at(earliest, SLEEP_NIGHT_OPEN, days=0 if local.hour >= 12 else -1)
        for _ in range(2):
            wake_by = msk_at(evening, SLEEP_WAKE_BY, days=1)
            preferred = evening
            if self.bulls_pending(evening):
                preferred = msk_at(evening, SLEEP_AFTER_BULLS, days=1)
            latest = wake_by - duration
            preferred = min(preferred, latest)
            if earliest <= latest:
                return max(earliest, preferred)
            evening += timedelta(days=1)
        return earliest

    def bulls_pending(self, evening: datetime) -> bool:
        """Инвайтов биржевиков стоит ждать в ночь, начинающуюся вечером `evening`."""
        if not self.feature_on("bulls_join") or self.cfg.chats.bulls_invite_chat_id is None:
            return False
        won: datetime | None = self.value("bulls_won_at")
        return won is None or won < night_start(evening)

    def sleep_runs(self) -> bool:
        """Сон будет исполнен: механика включена, а в `live` сценарий ещё и сертифицирован."""
        if not self.feature_on("sleep"):
            return False
        return self.certified is None or "sleep" in self.certified

    def night_hotel(self) -> int:
        """Деньги на отель в ближайший сон, если спать в отеле: после слива выбор не меняется."""
        if not self.sleep_runs() or self.value("sleep_deadline") is None:
            return 0
        if not self.hotel():
            return 0
        return self.hotel_threshold() or 0

    def sleep_place(self) -> Literal["hotel", "bridge"] | None:
        """Место сна по тому же правилу, что резерв на отель, на текущих деньгах; None — деньги
        неизвестны или цена отеля не видена (оценка 3💵 за уровень для подсказки не годится)."""
        if self.value("money") is None or "hotel" not in self.s.prices:
            return None
        return "hotel" if self.hotel() else "bridge"

    def ticket_affordable(self) -> bool:
        """Билет Горбушки по карману: деньги сверх резерва на отель и знания."""
        money, knowledge = self.value("money"), self.value("knowledge")
        if money is None or knowledge is None:
            return False
        ticket = self.ticket()
        return bool(money - self.hotel_reserve() >= ticket.money and knowledge >= ticket.knowledge)

    def hotel_reserve(self) -> int:
        """💵, которые дела, лотерея и билет Горбушки не тратят за 3 часа до сна в отеле: сколько
        сценарий сна потребует для отеля (большее из цены и порога) — трата сверх одной цены
        увела бы сон под мост."""
        deadline: datetime | None = self.value("sleep_deadline")
        if not self.sleep_runs() or deadline is None:
            return 0
        need = self.hotel_threshold()
        window = self.sleep_start(deadline) - HOTEL_RESERVE_AHEAD
        if self.now >= window and need is not None and self.hotel():
            return need
        return 0


def _complete(snap: LotteryState | None) -> bool:
    return snap is not None and all(
        v is not None and set(v) >= set(LOTTERY_CURRENCIES)
        for v in (snap.bought, snap.limits, snap.prices)
    )
