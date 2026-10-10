from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from app.engine.artifact import artifact_deeds, collecting
from app.engine.planner.types import Act, Candidate, Decision, Reserve, Wait, WakeKind, Wakeup
from app.engine.settings import Settings
from app.engine.state.model import (
    DEFAULT_PRICES,
    BusyState,
    CharacterState,
    GorbushkaState,
    PriceState,
    stale_fields,
)

# «Скоро Битва» замечено до ~6 мин до начала, «Битва уже началась» — в первую минуту.
BATTLE_BEFORE = timedelta(minutes=6)
BATTLE_AFTER = timedelta(minutes=1)
# Отсчёт до битвы с секундами может указать чуть позже начала часа (HH:00:00.4).
BATTLE_SKEW = timedelta(minutes=2)
# Отсчёт от суток игра показывает с точностью до часа: двухминутный запас увёл бы отсчёт, снятый в
# первые минуты часа, на час раньше, поэтому запас — только на задержку отправки.
HOURLY_COUNTDOWN = timedelta(days=1)
HOURLY_SKEW = timedelta(seconds=2)
GORBUSHKA_TICKET = PriceState(money=120, knowledge=20)
# Таймеры выведены из даты сообщения (точность — секунда): итог приходит в `until` + 0–1 с.
TIMER_MARGIN = timedelta(seconds=3)
# Кулдауны на экранах — с точностью до минуты, округлены вниз: готовность до 59 с позже.
READY_SLACK = timedelta(minutes=1)
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
    "company",
    "team_tag",
)
SOURCE = {
    **dict.fromkeys(_PROFILE, "profile"),
    **dict.fromkeys(
        ("books", "cards", "book_ready_at", "card_ready_at", "prizebox", "prizebox_ready_at"),
        "inventory",
    ),
    **dict.fromkeys(("food_stock", "fastfood_ready_at"), "food"),
    **dict.fromkeys(
        ("containers_small", "containers_medium", "tangerines", "tangerine_gifts"), "gifts"
    ),
    "gorbushka": "gorbushka",
    **dict.fromkeys(("artifacts", "artifact_collect"), "artifacts"),
    "startup": "startup",
    **dict.fromkeys(("gadgets", "bag", "bag_cap"), "inventory"),
    **dict.fromkeys(("upgrades", "upgrade_info"), "upgrades"),
    **dict.fromkeys(("stock_holdings", "stock_quotes", "stock_limits"), "stocks"),
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
    "battle_target": "battle",
    "battle_stamina": "battle",
    "stocks_dump": "stocks_dump",
    "factory_signup": "factory",
    "factory_report": "factory",
    "bulls_join": "bulls",
    "tangerine": "tangerine",
    "tangerine_gifts": "tangerine_gifts",
    "smoothie": "smoothie",
    "metro": "metro",
    "metro_resume": "metro",
    "daily_refresh": "daily_tasks",
    "daily_pick": "daily_tasks",
    "team_pick": "team_pick",
    "lottery_buy": "lottery",
    "trip": "trips",
    "trips_refresh": "trips",
    "gadget_buy": "gadgets_buy",
    "gadget_wear_set": "gadgets_buy",
}
# Ключ кулдауна проверок выхода из метро: свой, не общий с забегом.
METRO_PROBE = "metro_probe"
# Неподтверждённый выход без известной битвы забега держит планировщик не дольше этого (после
# второй проверки `/main`).
EXIT_HOLD_NO_BATTLE = timedelta(hours=3)
# В режиме сбора артефакта 🔥 тратят только его дела: вход в метро и бой Горбушки выключены.
ARTIFACT_OFF = frozenset({"gorbushka", "metro"})

Step = Callable[[BusyState | None], Decision | None]


def battle_hour(at: datetime, seen: datetime | None = None) -> datetime:
    """Битва — ровно в начале часа, отсчёт до неё округлён вниз: ближайший час не раньше.

    `seen` — момент, когда отсчёт показан: по его длине видна точность.
    """
    hourly = seen is not None and at - seen >= HOURLY_COUNTDOWN
    shifted = at - (HOURLY_SKEW if hourly else BATTLE_SKEW)
    hour = shifted.replace(minute=0, second=0, microsecond=0)
    return hour if hour == shifted else hour + timedelta(hours=1)


def exit_unconfirmed(state: CharacterState, now: datetime) -> bool:
    """Итог забега показан, выход не подтверждён, а битва забега ещё не прошла: персонаж,
    возможно, в метро. После битвы он снаружи наверняка — даже если выброс не распознан."""
    seen = state.metro_message
    run = seen.value if seen is not None else None
    if run is None or run.exit_at is None:
        return False
    known = run.battle_at
    if known is None:
        return now < run.exit_at + EXIT_HOLD_NO_BATTLE
    return now < battle_hour(known.value, known.at)


class PlannerBase:
    def __init__(
        self,
        state: CharacterState,
        settings: Settings,
        now: datetime,
        certified: frozenset[str] | None,
        last_refresh: Mapping[str, datetime],
        cooldowns: Mapping[str, datetime],
        last_done: Mapping[str, datetime],
        metro_durations: Sequence[float] = (),
        done_today: Mapping[str, int] | None = None,
        metro_probes: Sequence[str] = (),
    ) -> None:
        self.s = state
        self.cfg = settings
        self.now = now
        self.certified = certified
        self.last_refresh = last_refresh
        self.cooldowns = cooldowns
        self.last_done = last_done
        self.metro_durations = metro_durations
        self.done_today: Mapping[str, int] = done_today or {}
        # Проверки выхода из метро (`/main`, `/compact`), сделанные после итога забега.
        self.metro_probes: Sequence[str] = metro_probes
        # Итог забега показан, выход не подтверждён: персонаж, возможно, ещё в метро и игра молчит
        # на команды — шлём только проверки выхода.
        self.exit_unconfirmed = exit_unconfirmed(state, now)
        self.volatile_age = timedelta(minutes=settings.engine.state_stale_after_min)
        self.stale = self.find_stale()
        self.refresh_every = timedelta(seconds=settings.engine.refresh_min_interval_s)
        self.candidates: list[Candidate] = []
        self.wakeups: list[Wakeup] = []

    def find_stale(self, *, last_known: bool = False) -> frozenset[str]:
        """Устаревшие поля. `last_known` — план «по последним данным»: быстрые поля по возрасту и
        🔥 по тику регенерации не устаревают — берутся последние известные значения; сомнительные и
        ненаблюдавшиеся, медленные поля и прошедшая битва — как обычно."""
        volatile = timedelta(minutes=self.cfg.engine.state_stale_after_min)
        stale = set(stale_fields(self.s, self.now, timedelta.max if last_known else volatile))
        # После тика регенерации 🔥 наблюдение мотивации устарело независимо от возраста.
        regen = self.s.motivation_next_at
        seen = self.s.motivation
        if seen is not None and regen is not None and regen.value is not None and not last_known:
            if seen.at < regen.value and regen.value + TIMER_MARGIN <= self.now:
                stale.add("motivation")
        # Прошедшая битва: время следующей известно только из свежего профиля.
        battle = self.battle_time()
        if battle is not None and battle + BATTLE_AFTER <= self.now:
            stale.add("battle_at")
        return frozenset(stale)

    def value(self, name: str) -> Any:
        obs = getattr(self.s, name)
        return None if obs is None else obs.value

    def due(self, at: datetime, seen: datetime | None = None) -> bool:
        """Таймер истёк с запасом; истёкший уже на момент наблюдения `seen` — без запаса."""
        return (seen is not None and at <= seen) or at + TIMER_MARGIN <= self.now

    def wake(self, at: datetime | None, kind: WakeKind, key: str | None = None) -> None:
        if at is not None and at + TIMER_MARGIN > self.now:
            self.wakeups.append(Wakeup(at + TIMER_MARGIN, kind, key))

    def reject(self, scenario: str, params: Mapping[str, Any], verdict: str) -> None:
        self.candidates.append(Candidate(scenario, dict(params), None, verdict))

    def artifact_mode(self) -> bool:
        """Идёт сбор артефакта: вся 🔥 — в дела его тактики."""
        return collecting(self.cfg.artifact_run, self.now)

    def artifact_deeds(self) -> tuple[str, ...]:
        run = self.cfg.artifact_run
        return artifact_deeds(self.cfg.artifacts, run.artifact, self.value("level"))

    def artifact_blocks(self, scenario: str) -> bool:
        """Режим сбора выключает траты 🔥 вне дел тактики; еда перед битвой 🔥 не тратит."""
        if not self.artifact_mode():
            return False
        if scenario in ARTIFACT_OFF:
            return True
        deed = scenario.removeprefix("deed:")
        return scenario != deed and deed not in ("", "eat") and deed not in self.artifact_deeds()

    def artifact_reject(self, scenario: str) -> bool:
        """Механику выключил режим сбора: вердикт `artifact_run`, если без режима она была бы
        включена."""
        if not self.artifact_blocks(scenario):
            return False
        feature = "deeds" if scenario.startswith("deed:") else FEATURE.get(scenario)
        if feature is None or getattr(self.cfg.features, feature):
            self.reject(scenario, {}, "artifact_run")
        return True

    def feature_on(self, scenario: str) -> bool:
        if self.artifact_blocks(scenario):
            return False
        feature = "deeds" if scenario.startswith("deed:") else FEATURE.get(scenario)
        return feature is None or bool(getattr(self.cfg.features, feature))

    def gate(self, scenario: str, key: str | None = None) -> str | None:
        """Сертификация — по имени сценария, кулдаун — по ключу (у рефреша он свой на источник)."""
        if self.certified is not None and scenario not in self.certified:
            return "uncertified"
        key = key or scenario
        until = self.cooldowns.get(key)
        if until is not None and until > self.now:
            self.wake(until, "cooldown", key)
            return "cooldown"
        return None

    def gated(self, scenario: str, key: str | None = None) -> bool:
        """`gate` отказал бы — без его таймера: для вердикта, а не для решения."""
        if self.certified is not None and scenario not in self.certified:
            return True
        until = self.cooldowns.get(key or scenario)
        return until is not None and until > self.now

    def _no_team_seen(self) -> timedelta | None:
        """Сколько назад профиль показал персонажа без тега команды; None — тег есть или
        неизвестен."""
        team = self.s.team_tag
        return None if team is None or team.value is not None else self.now - team.at

    def teamless(self) -> bool:
        """В свежем профиле (не старше быстрых полей) нет тега команды: задания дня и фабрика —
        только для команд."""
        age = self._no_team_seen()
        return age is not None and age <= self.volatile_age

    def teamless_before(self) -> bool:
        """Тега команды не было в давнем профиле: игрок мог вступить в команду — до отказа
        `no_team` нужен свежий профиль, а пока — как в команде."""
        age = self._no_team_seen()
        return age is not None and age > self.volatile_age

    def stale_of(self, *fields: str) -> str | None:
        for name in fields:
            if getattr(self.s, name) is None or name in self.stale:
                return name
        return None

    def act(
        self, scenario: str, params: Mapping[str, Any], reason: str, key: str | None = None
    ) -> Act | None:
        if self.exit_unconfirmed and key != METRO_PROBE:
            self.reject(scenario, params, "metro_stuck")
            return None
        if (why := self.gate(scenario, key)) is not None:
            self.reject(scenario, params, why)
            return None
        self.candidates.append(Candidate(scenario, dict(params), None, "chosen"))
        return Act(scenario, dict(params), reason, tuple(self.candidates))

    def refresh(self, scenario: str, field: str) -> Act | None:
        """Нужное поле неизвестно или устарело: обновить источник, если позволяет лимит."""
        source = SOURCE[field]
        self.reject(scenario, {}, f"stale:{field}")
        if self.battle_running():
            # В первую минуту битвы игра отвечает на любой экран «Битва уже началась».
            self.reject("refresh", {"source": source}, "battle_window")
            return None
        last = self.last_refresh.get(source)
        if last is not None and self.now - last < self.refresh_every:
            self.wake(last + self.refresh_every, "refresh", source)
            return None
        return self.act(
            "refresh", {"source": source}, f"{scenario} needs {field}", f"refresh:{source}"
        )

    def wait(self) -> Wait:
        if not self.wakeups:
            return Wait(None, "no_timers", tuple(self.candidates))
        first = min(self.wakeups, key=lambda w: (w.at, w.reason))
        return Wait(first.at, first.reason, tuple(self.candidates))

    def busy(self) -> BusyState | None:
        obs = self.s.busy
        if obs is None or obs.src == "doubtful" or obs.value is None:
            return None
        return None if self.due(obs.value.until) else obs.value

    def timer(self, name: str) -> datetime | None:
        """Момент готовности по таймеру-полю; None — уже готово (с учётом запаса)."""
        obs = getattr(self.s, name)
        if obs is None or obs.value is None or obs.value <= obs.at:
            return None
        at: datetime = obs.value + READY_SLACK
        return None if self.due(at) else at

    def pick_food(self) -> str | None:
        stamina: int = self.value("stamina")
        stock = self.value("food_stock")
        for kind in self.cfg.food.order:
            item = stock.get(kind)
            reserve = self.cfg.food.banana_reserve if kind == "banana" else 0
            if item is not None and item.count > reserve and stamina < item.low:
                return kind
        return None

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

    def battle_time(self) -> datetime | None:
        """Время битвы по наблюдению, приведённое к началу часа; свежесть не проверяется."""
        obs = self.s.battle_at
        return None if obs is None else battle_hour(obs.value, obs.at)

    def battle_blocks(self, end: datetime) -> bool:
        """Занятие до `end` задевает окно битвы (`BATTLE_BEFORE` до неё — `BATTLE_AFTER` после):
        цикл проснётся к концу окна."""
        battle = self.battle_time()
        if battle is None or self.now >= battle + BATTLE_AFTER or end <= battle - BATTLE_BEFORE:
            return False
        self.wake(battle + BATTLE_AFTER, "battle")
        return True

    def battle_reject(self, scenario: str) -> bool:
        """Действие сейчас попало бы в окно битвы: игра откажет «Скоро Битва» — отказ
        `battle_window`."""
        if not self.battle_blocks(self.now):
            return False
        self.reject(scenario, {}, "battle_window")
        return True

    def battle_running(self) -> bool:
        """Битва идёт: от её начала до `BATTLE_AFTER`; к концу цикл проснётся."""
        battle = self.battle_time()
        if battle is None or not battle <= self.now < battle + BATTLE_AFTER:
            return False
        self.wake(battle + BATTLE_AFTER, "battle")
        return True

    def upcoming_battle(self) -> datetime | None:
        if self.stale_of("battle_at") is not None:
            return None
        battle = self.battle_time()
        return battle if battle is not None and battle > self.now else None

    # --- резервы

    def hotel_cost(self) -> int | None:
        known = self.s.prices.get("hotel")
        level: int | None = self.value("level")
        if known is not None:
            return known.value.money
        return 3 * level if level is not None else None

    def hotel_threshold(self) -> int | None:
        """Сколько 💵 (сверх билета Горбушки) нужно, чтобы спать в отеле, а не под мостом: как
        у сценария сна — большее из цены отеля и порога (порог ниже цены отель не удешевит)."""
        threshold = self.cfg.sleep.hotel_if_cash_after_reserve_ge
        cost = self.hotel_cost()
        if threshold is None or cost is None:
            return cost if threshold is None else threshold
        return max(cost, threshold)

    def hotel(self) -> bool:
        threshold = self.hotel_threshold()
        if threshold is None or self.value("money") is None:
            return False
        return int(self.value("money")) - self.ticket_reserve() >= threshold

    def gorbushka_state(self) -> GorbushkaState | None:
        if self.stale_of("gorbushka") is not None:
            return None
        state: GorbushkaState = self.value("gorbushka")
        return state

    def ticket(self) -> PriceState:
        known = self.s.prices.get("gorbushka_ticket")
        return known.value if known is not None else GORBUSHKA_TICKET

    def gorbushka_hold(self) -> Reserve | None:
        """🔥 под бой Горбушки, которые дела не тратят: бой не позже чем через
        `strategy.reserve_ahead_min.gorbushka` минут."""
        g = self.gorbushka_state() if self.feature_on("gorbushka") else None
        if g is None or g.state not in ("meeting", "waiting"):
            return None
        if g.won is not None and g.total is not None and g.won >= g.total:
            return None
        ahead = timedelta(minutes=self.cfg.strategy.reserve_ahead_min.gorbushka)
        fight_at = g.next_fight_at or self.now
        if not ahead or fight_at - self.now > ahead:
            return None
        cost = g.fight_cost if g.fight_cost is not None else 1
        return Reserve("gorbushka", cost, max(fight_at, self.now))

    def motivation_reserve(self) -> int:
        reserve = self.gorbushka_hold()
        return 0 if reserve is None else reserve.motivation

    def ticket_reserve(self) -> int:
        g = self.gorbushka_state() if self.feature_on("gorbushka") else None
        return self.ticket().money if g is not None and g.state == "need_ticket" else 0
