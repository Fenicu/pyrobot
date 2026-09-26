from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta

from app.engine.gametime import MSK, to_msk
from app.engine.market import pick_stock
from app.engine.parsing.smoothie import INGREDIENTS
from app.engine.planner.base import BATTLE_BEFORE, READY_SLACK, PlannerBase, battle_hour
from app.engine.planner.types import Decision
from app.engine.state.model import BusyState, StockLimits, TargetSet

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
NIGHT_START, NIGHT_END = time(22, 0), time(8, 0)
SLEEP_NIGHT_OPEN, SLEEP_AFTER_BULLS, SLEEP_WAKE_BY = time(22, 5), time(0, 30), time(12, 45)
SMOOTHIE_RESET = time(3, 0)


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
        desired = self.cfg.battle.overrides.get(to_msk(battle).hour, self.cfg.battle.target)
        if self.target_ready(battle, desired):
            return None
        return self.act("battle_target", {"target": desired}, "battle_target")

    def target_ready(self, battle: datetime, desired: str) -> bool:
        seen, known = self.s.battle_target, self.s.battle_at
        # Время предстоящей битвы видно только после прошлой: цель, снятая вместе с ним или
        # позже, относится к предстоящей битве.
        if seen is not None and known is not None and seen.at >= known.at:
            if seen.value is not None:
                return bool(seen.value == desired)
        # Защиту профиль не показывает — опора на то, какая цель и на какую битву выставлена.
        done: TargetSet | None = self.value("battle_target_set")
        if done is None or done.target != desired:
            return False
        return battle_hour(done.battle_at) == battle

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
        if (field := self.stale_of("money")) is not None:
            return self.refresh("stocks_dump", field)
        keep = self.cfg.stocks.cash_floor + self.ticket_reserve() + self.night_hotel()
        # После покупки игра оставляет не меньше неснижаемого остатка биржи.
        floor = max(keep, limits.reserve) if limits is not None else keep
        if self.value("money") - floor < self.cfg.stocks.min_dump:
            return None
        # Котировки меняются после каждой битвы: предпроверка — по увиденным в окне слива.
        quotes = self.s.stock_quotes
        margin = self.cfg.stocks.sell_cap_margin
        if quotes is not None and quotes.at >= start and limits is not None:
            if not pick_stock(quotes.value, limits.min_buy, limits.max_sell, margin):
                self.reject("stocks_dump", {}, "no_stock")
                return None
        return self.act("stocks_dump", {"keep": keep, "margin": margin}, "battle_soon")

    # --- фабрика

    def factory_pending(self) -> bool:
        """Запись на сегодняшнюю битву за фабрику ещё нужна."""
        if not self.feature_on("factory_signup"):
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
        if not self.factory_pending():
            return None
        opens = msk_at(self.now, FACTORY_OPEN)
        if self.now < opens:
            self.wake(opens, "factory_open")
            return None
        if busy is not None:
            self.reject("factory_signup", {}, "busy")
            return None
        return self.act("factory_signup", {}, "factory_window")

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
        if not self.feature_on("tangerine"):
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
        chats = self.cfg.chats
        params = {"chat": chats.tangerine_chat_id, "reply_to": chats.tangerine_reply_to}
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
        need = Counter(INGREDIENTS[fruit] for fruit in recipe.value.recipe)
        if stock is not None and stock.at >= day:
            if any(stock.value.get(name, 0) < n for name, n in need.items()):
                return None
        if busy is not None:
            self.reject("smoothie", {}, "busy")
            return None
        return self.act("smoothie", {"recipe": recipe.value.recipe}, "smoothie_recipe")

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
        return max(self.hotel_cost() or 0, self.hotel_threshold() or 0)

    def hotel_reserve(self) -> int:
        deadline: datetime | None = self.value("sleep_deadline")
        if not self.sleep_runs() or deadline is None:
            return 0
        cost = self.hotel_cost()
        window = self.sleep_start(deadline) - HOTEL_RESERVE_AHEAD
        if self.now >= window and cost is not None and self.hotel():
            return cost
        return 0
