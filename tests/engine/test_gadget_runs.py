"""Задача заточки (`gadget_upgrade`): переходы с `task_id`, итоги порций, сверка по состоянию и
уведомления покупки, сетов и заточки."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.engine.gadgets import (
    GadgetConflict,
    GadgetRuns,
    end_upgrade,
    start_upgrade,
    stop_upgrade,
    task_view,
)
from app.engine.notify import Level
from app.engine.scenarios.library import ScenarioResult
from app.engine.settings import GadgetUpgradeSection, Settings, StaticSettings
from app.engine.state.model import (
    CharacterState,
    GadgetsState,
    GadgetState,
    Obs,
    UpgradeInfo,
    Upgrades,
)
from app.engine.state.model import Src as ObsSrc

T0 = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)
PHONE = "Китайская мобила"


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return 0.0


class Notes:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    @property
    def codes(self) -> list[str]:
        return [code for _, code, _ in self.items]

    async def notify(self, level: Level, code: str, text: str) -> None:
        self.items.append((level, code, text))


def phone(level: int | None = 3, name: str = PHONE) -> GadgetState:
    return GadgetState(
        grade="⚪️" if level else None,
        level=level,
        slot="📱",
        name=name,
        bonuses={"practice": 1},
        code="p1",
    )


def worn(*items: GadgetState, at: datetime = T0, src: ObsSrc = "screen") -> CharacterState:
    return CharacterState(gadgets=Obs(value=GadgetsState(items=items), at=at, src=src))


class Rig:
    def __init__(self, state: CharacterState | None = None, settings: Settings | None = None):
        self.settings = StaticSettings(settings or Settings())
        self.state = state if state is not None else worn(phone())
        self.notes = Notes()
        self.clock = Clock(T0)
        self.runs = GadgetRuns(
            settings=self.settings,
            state=lambda: self.state,
            notifier=self.notes,
            clock=self.clock,
        )

    @property
    def task(self) -> GadgetUpgradeSection:
        return self.settings.current.gadget_upgrade

    def upgrade(self, task_id: int, status: str, reason: str, level: int = 5) -> Any:
        details = {"task_id": task_id, "level": level, "attempts": 4, "ok": 2, "fail": 2}
        result = ScenarioResult(status, reason, details)  # type: ignore[arg-type]
        return self.runs.after("gadget_upgrade", {"slot": "right"}, result)


async def test_start_increments_task_id_and_takes_worn_gadget() -> None:
    rig = Rig()
    await rig.runs.start("right", 10, "white", by="alice")
    task = rig.task
    assert (task.status, task.task_id, task.slot, task.gadget, task.kind) == (
        "active",
        1,
        "right",
        PHONE,
        "white",
    )
    assert (task.target, task.start_level, task.started_at) == (10, 3, T0)
    assert (task.end_level, task.ended_at, task.end_reason) == (None, None, None)
    view = task_view(rig.settings.current, rig.state)
    assert (view.status, view.task_id, view.gadget, view.level) == ("active", 1, PHONE, 3)


async def test_start_rejections() -> None:
    rig = Rig()
    await rig.runs.start("right", 10, "auto", by="alice")
    with pytest.raises(GadgetConflict) as running:
        await rig.runs.start("right", 12, "auto", by="alice")
    assert running.value.code == "upgrade_in_progress"
    empty = Rig()
    with pytest.raises(GadgetConflict) as not_worn:
        await empty.runs.start("left", 10, "auto", by="alice")
    assert not_worn.value.code == "not_worn"
    unknown = Rig(state=CharacterState())
    with pytest.raises(GadgetConflict) as no_list:
        await unknown.runs.start("right", 10, "auto", by="alice")
    assert no_list.value.code == "not_worn"
    reached = Rig()
    with pytest.raises(GadgetConflict) as low:
        await reached.runs.start("right", 3, "auto", by="alice")
    assert low.value.code == "target_reached"
    assert reached.task == GadgetUpgradeSection()


async def test_start_of_unupgraded_gadget_counts_level_zero() -> None:
    rig = Rig(state=worn(phone(level=None)))
    await rig.runs.start("right", 1, "white", by="alice")
    assert (rig.task.status, rig.task.start_level) == ("active", 0)


async def test_stop_then_restart_gets_new_task_id() -> None:
    rig = Rig()
    with pytest.raises(GadgetConflict) as idle:
        await rig.runs.stop(by="alice")
    assert idle.value.code == "no_task"
    await rig.runs.start("right", 10, "white", by="alice")
    rig.clock.at = T0 + MIN
    await rig.runs.stop(by="alice")
    task = rig.task
    assert (task.status, task.end_reason, task.ended_at, task.task_id) == (
        "stopped",
        "stopped",
        T0 + MIN,
        1,
    )
    with pytest.raises(GadgetConflict) as again:
        await rig.runs.stop(by="alice")
    assert again.value.code == "no_task"
    await rig.runs.start("right", 12, "red", by="alice")
    task = rig.task
    assert (task.status, task.task_id, task.target, task.kind) == ("active", 2, 12, "red")
    assert (task.started_at, task.ended_at, task.end_reason) == (T0 + MIN, None, None)


async def test_late_after_of_old_task_is_ignored() -> None:
    rig = Rig()
    await rig.runs.start("right", 10, "white", by="alice")
    await rig.runs.stop(by="alice")
    await rig.runs.start("right", 10, "white", by="alice")
    await rig.upgrade(1, "done", "target_reached", level=10)
    assert (rig.task.status, rig.task.task_id) == ("active", 2)
    assert rig.notes.codes == []
    # Итог без task_id задачу тоже не трогает.
    no_id = ScenarioResult("done", "target_reached", {"level": 10})
    await rig.runs.after("gadget_upgrade", {}, no_id)
    assert rig.task.status == "active" and rig.notes.codes == []


@pytest.mark.parametrize(
    ("status", "reason", "final", "code", "level"),
    [
        ("done", "target_reached", "done", "gadget_upgrade_done", "info"),
        ("done", "exhausted", "exhausted", "gadget_upgrade_exhausted", "warn"),
        ("nothing", "gadget_changed", "failed", "gadget_upgrade_failed", "warn"),
    ],
)
async def test_after_transitions_and_notifications(
    status: str, reason: str, final: str, code: str, level: str
) -> None:
    rig = Rig()
    await rig.runs.start("right", 10, "white", by="alice")
    rig.clock.at = T0 + 5 * MIN
    await rig.upgrade(1, status, reason, level=7)
    task = rig.task
    assert (task.status, task.end_reason, task.end_level, task.ended_at) == (
        final,
        reason,
        7,
        T0 + 5 * MIN,
    )
    assert [(lvl, c) for lvl, c, _ in rig.notes.items] == [(level, code)]
    # Повторный итог по закрытой задаче — ничего.
    await rig.upgrade(1, status, reason, level=7)
    assert len(rig.notes.items) == 1


@pytest.mark.parametrize(
    ("status", "reason"),
    [("done", "batch"), ("nothing", "busy"), ("failed", "target_reached"), ("nothing", "paused")],
)
async def test_other_upgrade_results_leave_task_active(status: str, reason: str) -> None:
    rig = Rig()
    await rig.runs.start("right", 10, "white", by="alice")
    await rig.upgrade(1, status, reason)
    assert rig.task.status == "active" and rig.notes.codes == []


async def test_tick_done_when_level_reached_by_hand() -> None:
    rig = Rig()
    await rig.runs.start("right", 5, "white", by="alice")
    await rig.runs.tick()
    assert rig.task.status == "active"
    rig.state = worn(phone(level=5), at=T0 - MIN)
    rig.clock.at = T0 + MIN
    await rig.runs.tick()
    task = rig.task
    assert (task.status, task.end_reason, task.end_level, task.ended_at) == (
        "done",
        "target_reached",
        5,
        T0 + MIN,
    )
    assert rig.notes.codes == ["gadget_upgrade_done"]
    await rig.runs.tick()
    assert rig.notes.codes == ["gadget_upgrade_done"]


async def test_tick_failed_when_other_gadget_on_slot() -> None:
    rig = Rig()
    await rig.runs.start("right", 10, "white", by="alice")
    # Сомнительный список рюкзака о смене не говорит.
    rig.state = worn(phone(name="iBlackM"), at=T0 + MIN, src="doubtful")
    await rig.runs.tick()
    assert rig.task.status == "active"
    rig.state = worn(phone(name="iBlackM"), at=T0 + MIN)
    await rig.runs.tick()
    assert (rig.task.status, rig.task.end_reason) == ("failed", "gadget_changed")
    assert [(lvl, c) for lvl, c, _ in rig.notes.items] == [("warn", "gadget_upgrade_failed")]


async def test_tick_failed_when_slot_emptied() -> None:
    rig = Rig()
    await rig.runs.start("right", 10, "white", by="alice")
    rig.state = worn(at=T0 + MIN)
    await rig.runs.tick()
    assert (rig.task.status, rig.task.end_reason) == ("failed", "gadget_changed")


def with_upgrades(
    state: CharacterState,
    stocks: Upgrades,
    *,
    info_at: datetime | None,
    at: datetime,
    src: ObsSrc = "screen",
) -> CharacterState:
    info = None if info_at is None else Obs(value=UpgradeInfo(), at=info_at)
    return state.model_copy(
        update={"upgrades": Obs(value=stocks, at=at, src=src), "upgrade_info": info}
    )


async def test_tick_exhausted_only_by_screen_after_start() -> None:
    rig = Rig()
    zero_white = Upgrades(white=0, blue=4, red=1)
    # Нулевой снимок `/upgrades` до старта задачу не закрывает.
    rig.state = with_upgrades(worn(phone()), zero_white, info_at=T0 - MIN, at=T0 - MIN)
    await rig.runs.start("right", 10, "white", by="alice")
    rig.clock.at = T0 + 5 * MIN
    await rig.runs.tick()
    assert rig.task.status == "active"
    # Производные запасы после попыток — тоже.
    rig.state = with_upgrades(
        worn(phone()), zero_white, info_at=T0 + MIN, at=T0 + 2 * MIN, src="derived"
    )
    await rig.runs.tick()
    assert rig.task.status == "active"
    # Экран `/up_` после старта (`upgrade_info` с `/upgrades` — до старта) — тоже.
    rig.state = with_upgrades(worn(phone()), zero_white, info_at=T0 - MIN, at=T0 + 2 * MIN)
    await rig.runs.tick()
    assert rig.task.status == "active"
    # Экран `/upgrades` после старта, но нужного вида хватает — задача идёт.
    plenty = Upgrades(white=3, blue=0, red=0)
    rig.state = with_upgrades(worn(phone()), plenty, info_at=T0 + MIN, at=T0 + MIN)
    await rig.runs.tick()
    assert rig.task.status == "active"
    rig.state = with_upgrades(worn(phone()), zero_white, info_at=T0 + 3 * MIN, at=T0 + 3 * MIN)
    await rig.runs.tick()
    task = rig.task
    assert (task.status, task.end_reason, task.end_level) == ("exhausted", "exhausted", 3)
    assert [(lvl, c) for lvl, c, _ in rig.notes.items] == [("warn", "gadget_upgrade_exhausted")]


async def test_tick_exhausted_auto_uses_white_until() -> None:
    settings = Settings.model_validate({"gadgets": {"white_until": 3}})
    rig = Rig(settings=settings)
    await rig.runs.start("right", 10, "auto", by="alice")
    # Уровень 3 не ниже `white_until`: авто точит 🔴, затем 🔵 — ⚪️ не в счёт.
    only_white = Upgrades(white=9, blue=0, red=0)
    rig.state = with_upgrades(worn(phone()), only_white, info_at=T0 + MIN, at=T0 + MIN)
    await rig.runs.tick()
    assert rig.task.status == "exhausted"


def bag(used: int, cap: int, *, src: ObsSrc = "screen") -> CharacterState:
    return worn(phone()).model_copy(
        update={"bag": Obs(value=used, at=T0, src=src), "bag_cap": Obs(value=cap, at=T0)}
    )


async def test_bag_full_notified_once_per_episode() -> None:
    off = Rig(state=bag(24, 24))
    await off.runs.tick()
    assert off.notes.codes == []
    rig = Rig(
        state=bag(24, 24), settings=Settings.model_validate({"features": {"gadgets_buy": True}})
    )
    await rig.runs.tick()
    await rig.runs.tick()
    assert [(lvl, c) for lvl, c, _ in rig.notes.items] == [("warn", "gadget_bag_full")]
    # Сомнительный счётчик эпизод не кончает.
    rig.state = bag(20, 24, src="doubtful")
    await rig.runs.tick()
    rig.state = bag(25, 24)
    await rig.runs.tick()
    assert rig.notes.codes == ["gadget_bag_full"]
    rig.state = bag(23, 24)
    await rig.runs.tick()
    rig.state = bag(24, 24)
    await rig.runs.tick()
    assert rig.notes.codes == ["gadget_bag_full", "gadget_bag_full"]


async def test_buy_and_wear_set_notifications() -> None:
    rig = Rig()
    bought = {
        "bought": "Китайская мобила",
        "price": 9,
        "rule": "empty",
        "slot": "right",
        "tier": 1,
        "worn": True,
        "sold": [],
    }
    await rig.runs.after("gadget_buy", {}, ScenarioResult("done", "bought", bought))
    sold = {
        **bought,
        "bought": "iBlackM",
        "price": 4449,
        "rule": "set",
        "sold": [
            {"company": "stark", "n": 51, "price": 80},
            {"company": "piper", "n": 4, "price": 60},
        ],
    }
    await rig.runs.after("gadget_buy", {}, ScenarioResult("done", "bought", sold))
    mismatch = {
        "slot": "right",
        "tier": 8,
        "seen": {"name": "iBlackM", "price": 4500, "level": 30},
    }
    await rig.runs.after("gadget_buy", {}, ScenarioResult("nothing", "shop_mismatch", mismatch))
    await rig.runs.after("gadget_buy", {}, ScenarioResult("nothing", "cant_afford", None))
    for active in (True, False, None):
        details = {
            "set": "summer",
            "active": active,
            "sets_before": ["⚫️Сет VIP"],
            "sets": ["🌞Летний сет"] if active is not False else [],
        }
        await rig.runs.after("gadget_wear_set", {}, ScenarioResult("done", "worn", details))
    await rig.runs.after("gadget_wear_set", {}, ScenarioResult("failed", "missing_item", None))
    assert [(lvl, c) for lvl, c, _ in rig.notes.items] == [
        ("info", "gadget_bought"),
        ("info", "gadget_bought"),
        ("warn", "gadget_shop_mismatch"),
        ("info", "gadget_set_worn"),
        ("warn", "gadget_set_inactive"),
        ("info", "gadget_set_unconfirmed"),
    ]
    texts = [text for _, _, text in rig.notes.items]
    assert texts[0] == "bought Китайская мобила for $9 (empty)"
    assert texts[1] == "bought iBlackM for $4449 (set), sold 55 shares"
    assert texts[2].startswith("shop right tier 8 differs from catalog: ")
    assert "iBlackM" in texts[2] and "4500" in texts[2]
    assert "⚫️Сет VIP" in texts[5] and "🌞Летний сет" in texts[5]


async def test_copy_from_bag_worn_is_not_a_purchase() -> None:
    rig = Rig()
    details = {"gadget": PHONE, "rule": "empty", "slot": "right", "tier": 1, "worn": True}
    await rig.runs.after("gadget_buy", {}, ScenarioResult("done", "worn", details))
    assert rig.notes.items == []


def test_pure_transitions_reject_without_active_task() -> None:
    idle = Settings()
    with pytest.raises(GadgetConflict) as stop:
        stop_upgrade(idle, T0)
    assert stop.value.code == "no_task"
    with pytest.raises(GadgetConflict) as end:
        end_upgrade(idle, "done", "target_reached", 5, T0)
    assert end.value.code == "no_task"
    active = start_upgrade(idle, worn(phone()), "right", 10, "blue", T0)
    ended = end_upgrade(active, "done", "target_reached", 10, T0 + MIN)
    assert ended.gadget_upgrade.model_dump() == {
        **active.gadget_upgrade.model_dump(),
        "status": "done",
        "end_reason": "target_reached",
        "end_level": 10,
        "ended_at": T0 + MIN,
    }
