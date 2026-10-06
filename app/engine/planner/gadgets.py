"""Шаги гаджетов: надеть набранный сет и купить гаджет — перед делами, заточка — после дел."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from app.engine.gadgets import (
    UPGRADE_BATCH,
    BuyPlan,
    WearSet,
    buy_plan,
    gear_guard,
    gear_until,
    in_dump_window,
    stocks_since,
)
from app.engine.gametime import to_msk
from app.engine.planner.base import BATTLE_AFTER
from app.engine.planner.obligations import Obligations, msk_at
from app.engine.planner.types import Decision
from app.engine.settings import Settings
from app.engine.state.model import BusyState, CharacterState

# Без них план покупки не решается: после покупки список рюкзака сомнителен до свежего /inv.
BUY_FIELDS = ("gadgets", "bag", "bag_cap", "level", "money", "company")
_PLAN_SHORT = ("cant_afford", "saving")


class GadgetSteps(Obligations):
    """Покупка и надевание гаджетов (механика `gadgets_buy`) и порции задачи заточки."""

    def reserve_unknown(self) -> str | None:
        """Поле, без которого резерв покупки неизвестен: не ноль, а повод обновить экран."""
        if self.feature_on("gorbushka") and self.stale_of("gorbushka") is not None:
            return "gorbushka"
        if self.sleep_runs() and self.stale_of("sleep_deadline") is not None:
            return "sleep_deadline"
        return None

    def gadget_reserve(self) -> int | None:
        if self.reserve_unknown() is not None:
            return None
        return self.cfg.gadgets.keep_money + self.ticket_reserve() + self.night_hotel()

    def gadget_plan(self) -> BuyPlan | None:
        reserve = self.gadget_reserve()
        if reserve is None:
            return None
        return buy_plan(self.s, self.cfg, reserve, self.value("company"), self.now)

    def gear_blocked(self, scenario: str, params: dict[str, Any]) -> bool:
        """Окно-запрет смены навыков: отказ с его вердиктом и пробуждение к концу окна (выход из
        метро будит `metro_kick`; прошедшее время боя Горбушки — не таймер)."""
        guard = gear_guard(self.s, self.now)
        if guard is None:
            return False
        verdict, end = guard
        self.reject(scenario, params, verdict)
        self.wake(end, "gear_guard")
        return True

    def gadget_wear_set(self, busy: BusyState | None) -> Decision | None:
        name = "gadget_wear_set"
        if not self.feature_on(name) or self.stale_of(*BUY_FIELDS) is not None:
            return None
        plan = self.gadget_plan()
        if plan is None or not isinstance(plan.action, WearSet):
            return None
        action = plan.action
        params: dict[str, Any] = {"set": action.set, "slots": list(action.slots)}
        task = self.cfg.gadget_upgrade
        if task.status == "active" and task.slot in action.slots:
            self.reject(name, params, "upgrade_running")
            return None
        if self.gear_blocked(name, params):
            return None
        return self.act(name, params, f"set {action.set} ready")

    def gadget_buy(self, busy: BusyState | None) -> Decision | None:
        name = "gadget_buy"
        if not self.feature_on(name):
            return None
        if (field := self.stale_of(*BUY_FIELDS) or self.reserve_unknown()) is not None:
            return self.refresh(name, field)
        plan = self.gadget_plan()
        assert plan is not None
        action = plan.action
        if isinstance(action, WearSet):
            return None
        if action is None:
            return self.no_buy(plan)
        params: dict[str, Any] = {
            "rule": action.rule,
            "slot": action.slot,
            "tier": action.tier,
            "price": action.price,
            "reserve": plan.money.reserve,
            "wear": action.wear,
            "in_bag": action.in_bag,
        }
        if action.sell_needed > 0 and self.market_closed(name, params):
            return None
        if not action.in_bag and in_dump_window(self.s, self.cfg, self.now):
            self.reject(name, params, "dump_window")
            battle = self.battle_time()
            self.wake(None if battle is None else battle + BATTLE_AFTER, "battle")
            return None
        if action.wear and self.gear_blocked(name, params):
            return None
        return self.act(name, params, f"{action.rule} {action.slot}{action.tier}")

    def no_buy(self, plan: BuyPlan) -> Decision | None:
        """Покупки нет. Нехватку денег при устаревших акциях сначала проверить по свежему /stock:
        биржа закрыта — ждать её открытия; экран уже открывали после прошлой битвы, а поля не
        обновились, — второй раз не поможет."""
        name = "gadget_buy"
        stale = plan.money.stale
        if plan.verdict in _PLAN_SHORT and stale is not None:
            if self.market_closed(name, {}):
                return None
            if not self.stocks_reread():
                return self.refresh(name, stale)
        self.reject(name, {}, plan.verdict)
        return None

    def stocks_reread(self) -> bool:
        last = self.last_refresh.get("stocks")
        if last is None:
            return False
        since = stocks_since(self.s, self.now)
        return since is None or last >= since

    def market_closed(self, scenario: str, params: dict[str, Any]) -> bool:
        """Биржа закрыта по часам лимитов: отказ `market_closed` и пробуждение к открытию."""
        seen = self.s.stock_limits
        if seen is None or seen.src == "doubtful":
            return False
        limits = seen.value
        if limits.open_hour <= to_msk(self.now).hour < limits.close_hour:
            return False
        self.reject(scenario, params, "market_closed")
        opens = msk_at(self.now, time(limits.open_hour))
        self.wake(opens if opens > self.now else opens + timedelta(days=1), "market_open")
        return True

    def gadget_upgrade(self, busy: BusyState | None) -> Decision | None:
        """Порция задачи заточки — и во время дела; запасы по снимку шаг не проверяет: сценарий
        прочитает `/upgrades` и закончит задачу по экрану."""
        task = self.cfg.gadget_upgrade
        if task.status != "active" or task.slot is None:
            return None
        name = "gadget_upgrade"
        params: dict[str, Any] = {
            "task_id": task.task_id,
            "slot": task.slot,
            "gadget": task.gadget,
            "target": task.target,
            "kind": task.kind or "auto",
            "white_until": self.cfg.gadgets.white_until,
            "batch": UPGRADE_BATCH,
        }
        if self.gear_blocked(name, params):
            return None
        until = gear_until(self.s, self.now)
        params["until"] = None if until is None else until.isoformat()
        return self.act(name, params, f"upgrade {task.slot} to {task.target}")


def buy_view(
    state: CharacterState,
    settings: Settings,
    now: datetime,
    *,
    certified: frozenset[str] | None = None,
) -> BuyPlan | None:
    """План покупки для API с резервом планировщика (без кулдаунов и журнала, как `outlook`);
    None — резерв неизвестен."""
    return GadgetSteps(state, settings, now, certified, {}, {}, {}).gadget_plan()
