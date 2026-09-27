import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

import app.engine.planner.loop as loop_module
from app.engine.bus import Delivery
from app.engine.gateway.types import Source
from app.engine.metro.store import MemoryMetroRunStore
from app.engine.notify import Level
from app.engine.parsing.food import FoodMenu
from app.engine.planner.base import TIMER_MARGIN
from app.engine.planner.loop import (
    DEEDS,
    MAX_RETRY,
    NOTHING_RETRY,
    RETRY_AFTER,
    FixedParams,
    InvalidParams,
    PlannerLoop,
)
from app.engine.planner.store import MemoryPlannerStore
from app.engine.planner.types import Act, Decision, Wait
from app.engine.scenarios.library import ScenarioResult
from app.engine.scenarios.registry import ScenarioSpec
from app.engine.settings import Settings
from app.engine.state.model import CharacterState
from tests.engine.fakegame import LIVE, World, running_world
from tests.engine.helpers import until
from tests.engine.planner.test_obligations import only, state
from tests.fixtures import game_msg


class ShiftClock:
    def __init__(self) -> None:
        self.shift = timedelta()

    def now(self) -> datetime:
        return datetime.now(UTC) + self.shift

    def monotonic(self) -> float:
        return time.monotonic()


class Notes:
    def __init__(self) -> None:
        self.codes: list[str] = []

    async def notify(self, level: Level, code: str, text: str) -> None:
        self.codes.append(code)


class Rig:
    def __init__(
        self,
        world: World,
        ready: str | None = None,
        auto: bool = True,
        store: MemoryPlannerStore | None = None,
        notes: Notes | None = None,
    ) -> None:
        self.world = world
        self.clock = ShiftClock()
        self.store = store or MemoryPlannerStore()
        self.notes = notes or Notes()
        self.ready = ready
        self.loop = PlannerLoop(
            gateway=world.gateway,
            state=lambda: world.state,
            settings=world.settings,
            clock=self.clock,
            store=self.store,
            notifier=self.notes,
            ready=lambda: self.ready,
            step_timeout_s=0.3,
            auto=auto,
        )

    async def steps(self, n: int) -> None:
        for _ in range(n):
            await self.loop.step()


# Цикл проверяется на механиках фазы 3; календарь фазы 4 — в test_obligations.py. Окна по часам
# (обязательства, ночной сон) выключены: часы здесь настоящие. Дело выбирается по оценке:
# чередование основных дел — в test_decide.py.
QUIET = LIVE.model_copy(
    update={
        "features": LIVE.features.model_copy(
            update={
                "stocks_dump": False,
                "factory": False,
                "bulls": False,
                "tangerine": False,
                "smoothie": False,
                "sleep": False,
                "metro": False,
                "daily_tasks": False,
                "lottery": False,
            }
        ),
        "strategy": LIVE.strategy.model_copy(update={"focus": ()}),
    }
)
DRY = QUIET.model_copy(update={"engine": QUIET.engine.model_copy(update={"mode": "dry_run"})})


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async for w in running_world(QUIET):
        yield w


@pytest.fixture
async def dry_world() -> AsyncIterator[World]:
    async for w in running_world(DRY):
        yield w


async def set_engine(world: World, **update: object) -> None:
    await world.settings.update(
        lambda s: s.model_copy(update={"engine": s.engine.model_copy(update=update)}),
        changed_by="test",
    )


def script_day(world: World) -> None:
    game = world.game
    game.on_text("😎Я", ("profile", 3624478))
    game.on_text("/inv", ("items", 3625102))
    game.on_text("/read_exp", ("items", 3516680))
    game.on_text("/to_eat", ("food", 3624997))
    game.on_text("/use_card", ("items", 3516678))
    game.on_text("/gifts", ("items", 3516682))
    game.on_text("/gorbushka", ("gorbushka", 3516741))
    game.on_text("/job", ("activities", 3623881))


async def test_from_empty_state_to_first_deed(world: World) -> None:
    script_day(world)
    rig = Rig(world)
    await rig.steps(9)
    assert world.game.payloads() == [
        "😎Я",
        "/inv",
        "/read_exp",
        "/to_eat",
        "/use_card",
        "/gifts",
        "/gorbushka",
        "/job",
    ]
    runs = [(r.scenario, r.status) for r in rig.store.runs]
    assert runs[-2:] == [("gorbushka", "nothing"), ("deed:job", "done")]
    last = rig.store.decisions[-1][1]
    assert (last.kind, last.reason) == ("wait", "gorbushka_next")
    gorbushka = world.state.gorbushka
    assert gorbushka is not None and gorbushka.value.next_fight_at is not None
    assert rig.loop.next_wake == gorbushka.value.next_fight_at + TIMER_MARGIN


async def test_scenario_steps_carry_their_run(world: World) -> None:
    script_day(world)
    rig = Rig(world)
    await rig.steps(9)
    run_of = {r.scenario: i for i, r in enumerate(rig.store.runs, start=1)}
    steps = {row.req.text: row.req.scenario_run_id for row in world.store.rows.values()}
    assert steps["/job"] == run_of["deed:job"]
    assert steps["/gorbushka"] == run_of["gorbushka"]
    assert None not in steps.values()


async def test_manual_run_steps_carry_their_run(world: World) -> None:
    world.game.on_text("/inv", ("items", 3625102))
    rig = Rig(world, auto=False)
    run_id, _ = await rig.loop.request("refresh", {"source": "inventory"}, key="k", by="admin")
    await rig.loop.run_manual()
    assert [
        (r.req.text, r.req.source, r.req.scenario_run_id) for r in world.store.rows.values()
    ] == [("/inv", Source.MANUAL, run_id)]


async def test_repeated_wait_recorded_once(world: World) -> None:
    script_day(world)
    rig = Rig(world)
    await rig.steps(12)
    waits = [d for _, d in rig.store.decisions if d.kind == "wait"]
    assert len(waits) == 1


async def test_not_ready_does_not_decide(world: World) -> None:
    rig = Rig(world, ready="paused")
    assert await rig.loop.step() == 5.0
    assert rig.store.decisions == [] and world.game.payloads() == []


async def test_not_ready_clears_next_wake(world: World) -> None:
    script_day(world)
    rig = Rig(world)
    await rig.steps(9)
    assert rig.loop.next_wake is not None
    rig.ready = "paused"
    await rig.loop.step()
    assert rig.loop.next_wake is None


async def test_dry_run_defers_suppressed_and_decides_the_rest(dry_world: World) -> None:
    script_day(dry_world)
    rig = Rig(dry_world)
    await rig.steps(12)
    assert dry_world.game.payloads() == ["😎Я", "/inv", "/to_eat", "/gifts", "/gorbushka"]
    runs = [(r.scenario, r.status) for r in rig.store.runs if r.status == "suppressed"]
    assert runs == [("book", "suppressed"), ("card", "suppressed"), ("deed:job", "suppressed")]
    held = {name for name, until in rig.loop._held.items() if until > rig.clock.now()}
    assert {"book", "card", "deed:job", "deed:harvest"} <= held
    assert rig.store.decisions[-1][1].kind == "wait"


async def test_switch_to_live_lifts_dry_run_holds(dry_world: World) -> None:
    script_day(dry_world)
    rig = Rig(dry_world)
    await rig.steps(12)
    assert "book" in rig.loop._held
    await set_engine(dry_world, mode="live")
    await rig.loop.step()
    assert dry_world.game.payloads()[-1] == "/read_exp"
    assert rig.store.runs[-1].status == "done"


async def test_kill_switch_suppression_is_not_held(world: World) -> None:
    await world.feed("profile", 3624478)
    await world.feed("items", 3625102)
    await world.gateway.kill("test")
    rig = Rig(world)
    await rig.loop.step()
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("book", "suppressed", "kill_switch")
    ]
    assert rig.loop._held == {} and rig.loop._cooldowns == {}


async def test_failures_cool_down_and_notify(world: World) -> None:
    rig = Rig(world)
    await rig.loop.step()
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("refresh", "failed", "timeout")
    ]
    await rig.loop.step()
    assert len(rig.store.runs) == 1
    assert rig.notes.codes == ["scenario_failed"]
    # Серия неудач: 5 мин, затем 10.
    for shift, runs in ((6, 2), (6, 2), (5, 3)):
        rig.clock.shift += timedelta(minutes=shift)
        await rig.loop.step()
        assert len(rig.store.runs) == runs
    assert rig.notes.codes == ["scenario_failed"]


def moment() -> datetime:
    return datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


async def test_failure_series_backs_off_until_done(world: World) -> None:
    rig = Rig(world)
    at = moment()
    act = Act("gorbushka", {"buy": False}, "gorbushka_fight")
    failed = ScenarioResult("failed", "timeout")
    spans = []
    for _ in range(3):
        await rig.loop._after(act, failed, at, at)
        spans.append(rig.loop._cooldowns["gorbushka"] - at)
    assert spans == [timedelta(minutes=m) for m in (5, 10, 20)]
    assert rig.notes.codes == ["scenario_failed"]
    await rig.loop._after(act, ScenarioResult("done", "gorbushka_fight"), at, at)
    await rig.loop._after(act, ScenarioResult("stopped", "unexpected_screen"), at, at)
    assert rig.loop._cooldowns["gorbushka"] - at == RETRY_AFTER
    assert rig.notes.codes == ["scenario_failed", "scenario_failed"]
    for _ in range(60):
        await rig.loop._after(act, failed, at, at)
    assert rig.loop._cooldowns["gorbushka"] - at == MAX_RETRY


async def test_refusal_cooldowns(world: World) -> None:
    rig = Rig(world)
    at = moment()
    job = Act("deed:job", {}, "best")
    await rig.loop._after(job, ScenarioResult("refused", "busy"), at, at)
    assert rig.loop._cooldowns == {"deed:job": at + timedelta(minutes=1)}
    await rig.loop._after(job, ScenarioResult("refused", "no_money"), at, at)
    assert rig.loop._cooldowns == {"deed:job": at + RETRY_AFTER}
    assert rig.notes.codes == []


async def test_daily_refresh_marks_last_refresh_even_on_failure(world: World) -> None:
    rig = Rig(world)
    at = moment()
    refresh = Act("daily_refresh", {}, "tasks unknown")
    await rig.loop._after(refresh, ScenarioResult("failed", "timeout"), at, at)
    assert rig.loop._last_refresh == {"daily": at}
    later = at + timedelta(minutes=11)
    await rig.loop._after(refresh, ScenarioResult("done", "screen"), later, later)
    assert rig.loop._last_refresh == {"daily": later}
    pick = Act("daily_pick", {"task": "convDets_hard"}, "personal convDets")
    await rig.loop._after(pick, ScenarioResult("nothing", "already_chosen"), later, later)
    assert rig.loop._cooldowns["daily_pick"] == later + NOTHING_RETRY


async def test_wrong_tasks_screen_backs_off_and_notifies_once(world: World) -> None:
    # Экран заданий сбился (игрок листает меню с телефона): пауза растёт, как у прочих неудач.
    rig = Rig(world)
    at = moment()
    pick = Act("daily_pick", {"task": "convDets_hard"}, "personal convDets")
    spans = []
    for _ in range(3):
        await rig.loop._after(pick, ScenarioResult("failed", "wrong_screen"), at, at)
        spans.append(rig.loop._cooldowns["daily_pick"] - at)
    assert spans == [timedelta(minutes=m) for m in (5, 10, 20)]
    assert rig.notes.codes == ["scenario_failed"]


@pytest.mark.parametrize(
    ("reason", "hold"),
    [
        ("lottery_closed", timedelta(hours=2)),
        ("no_draw", timedelta(minutes=30)),
        ("cant_afford", NOTHING_RETRY),
    ],
)
async def test_lottery_nothing_holds(world: World, reason: str, hold: timedelta) -> None:
    # Нет тиража или продажа закрыта: повтор раз в минуту только читал бы экран до конца окна.
    rig = Rig(world)
    at = moment()
    await rig.loop._after(
        Act("lottery_buy", {}, "lottery money"), ScenarioResult("nothing", reason), at, at
    )
    assert rig.loop._cooldowns == {"lottery_buy": at + hold}


def test_lottery_short_reads_details_regardless_of_status() -> None:
    # `buy_each` докупает часть валют и всё равно несёт нехватку по остальным: «done» не должен
    # прятать её от планировщика, как и «nothing» у cant_afford.
    done = ScenarioResult(
        "done", "bought_each", details={"lottery": {"draw": 3286, "short": {"knowledge": 21973}}}
    )
    assert loop_module._lottery_short(done) == (3286, {"knowledge": 21973})


@pytest.mark.parametrize(
    ("hour", "minute", "hold"),
    [
        # Тираж мог открыться на секунды позже 19:17: до 19:30 «тиража нет» повторяется скоро.
        (19, 17, timedelta(minutes=2)),
        (19, 29, timedelta(minutes=2)),
        (19, 30, timedelta(minutes=30)),
        (20, 45, timedelta(minutes=30)),
        # Ручной запуск до окна продажи — прежнее удержание.
        (19, 16, timedelta(minutes=30)),
    ],
)
async def test_no_draw_at_sale_start_is_retried_soon(
    world: World, hour: int, minute: int, hold: timedelta
) -> None:
    rig = Rig(world)
    at = datetime(2026, 9, 26, hour, minute, 30, tzinfo=MSK)
    no_draw = ScenarioResult("nothing", "no_draw")
    await rig.loop._after(Act("lottery_buy", {}, "lottery_unknown"), no_draw, at, at)
    assert rig.loop._cooldowns == {"lottery_buy": at + hold}


def lottery_only(**lottery: Any) -> Settings:
    """Из механик по часам — только лотерея (без сна: резерв отеля отдельно), движок — живой."""
    cfg = only("lottery", lottery=lottery)
    features = cfg.features.model_copy(update={"sleep": False})
    return cfg.model_copy(update={"engine": LIVE.engine, "features": features})


# Только 💵-билеты, и весь запас 💵 экрана (675) — неприкосновенный.
MONEY_KEPT = {"tickets": {"knowledge": 0, "raw": 0, "details": 0}, "keep": {"money": 675}}


@pytest.fixture
async def lottery_world() -> AsyncIterator[World]:
    async for w in running_world(lottery_only(**MONEY_KEPT)):
        w.game.on_text("/tickets", ("lottery", 3625282))
        w.game.on_text("/tickets_all", ("lottery", 3625321))
        yield w


def evening_clock() -> ShiftClock:
    """19:30 MSK послезавтра, дальше идут настоящим ходом: часы впереди настоящих — шлюз
    принимает ответы не раньше момента отправки."""
    clock = ShiftClock()
    real = datetime.now(UTC)
    day = (real + timedelta(days=2)).astimezone(MSK)
    clock.shift = datetime(day.year, day.month, day.day, 19, 30, tzinfo=MSK) - real
    return clock


async def test_lottery_cant_afford_waits_for_growth_not_staleness(lottery_world: World) -> None:
    """Сценарий не нашёл 💵 сверх запаса: 💵 с экрана устарели — не снова экран лотереи, а
    профиль; профиль без роста — лотерея не открывается."""
    world = lottery_world
    clock = evening_clock()
    world.game.clock = clock
    profile = game_msg("profile", 3624478)
    poor = replace(profile, text=(profile.text or "").replace("💵$867", "💵$675"))
    world.game.on_text("😎Я", poor)
    seen = ("lottery", "money", "knowledge", "raw", "details")

    def current() -> CharacterState:
        # Прочее состояние свежее на каждом шаге; лотерея и ресурсы — из конвейера.
        known = {k: v for k in seen if (v := getattr(world.state, k)) is not None}
        return state(clock.now()).model_copy(update=known)

    loop = PlannerLoop(
        gateway=world.gateway,
        state=current,
        settings=world.settings,
        clock=clock,
        store=MemoryPlannerStore(),
        notifier=Notes(),
        ready=lambda: None,
        step_timeout_s=0.3,
    )
    await loop.step()
    assert world.game.payloads() == ["/tickets"]
    clock.shift += timedelta(minutes=20)
    await loop.step()
    assert world.game.payloads() == ["/tickets", "😎Я"]
    await loop.step()
    assert world.game.payloads() == ["/tickets", "😎Я"]


async def test_manual_lottery_without_params_keeps_settings(lottery_world: World) -> None:
    # Без параметров ручной запуск берёт билеты, запасы и резерв, как планировщик: «все билеты
    # без запаса» потратили бы неприкосновенные 💵.
    world = lottery_world
    rig = Rig(world)
    run_id, _ = await rig.loop.request("lottery_buy", {}, key="l1", by="admin")
    await rig.loop.run_manual()
    run = rig.store.runs[run_id - 1]
    assert (run.status, run.reason) == ("nothing", "cant_afford")
    assert world.game.payloads() == ["/tickets"]
    # Присланный параметр важнее настроек.
    run_id, _ = await rig.loop.request("lottery_buy", {"keep_money": 0}, key="l2", by="admin")
    await rig.loop.run_manual()
    assert world.game.payloads()[1:3] == ["/tickets", "💵 => 🤑"]


async def test_closed_market_holds_dump_longer(world: World) -> None:
    rig = Rig(world)
    at = moment()
    dump = Act("stocks_dump", {"keep": 150, "margin": 5}, "battle_soon")
    await rig.loop._after(dump, ScenarioResult("nothing", "market_closed"), at, at)
    assert rig.loop._cooldowns == {"stocks_dump": at + timedelta(minutes=30)}
    await rig.loop._after(dump, ScenarioResult("nothing", "no_stock"), at, at)
    assert rig.loop._cooldowns == {"stocks_dump": at + NOTHING_RETRY}


async def test_changed_metro_price_holds_metro_for_hours(world: World) -> None:
    # Цена входа сама не вернётся: не повторять заход (и уведомление) каждую минуту.
    rig = Rig(world)
    at = moment()
    metro = Act("metro", {}, "metro_ready")
    await rig.loop._after(metro, ScenarioResult("nothing", "entry_cost_changed"), at, at)
    assert rig.loop._cooldowns == {"metro": at + timedelta(hours=2)}


async def test_battle_refusal_holds_all_deeds(world: World) -> None:
    world.game.on_text("/job", ("refusals", 3520502))
    await world.feed("profile", 3624478)
    await world.feed("gorbushka", 3516741)
    await world.settings.update(
        lambda s: s.model_copy(
            update={
                "features": s.features.model_copy(
                    update={"books": False, "cards_containers": False, "fastfood": False}
                )
            }
        ),
        changed_by="test",
    )
    rig = Rig(world)
    await rig.steps(2)
    assert world.game.payloads() == ["/job"]
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("deed:job", "refused", "battle_soon")
    ]
    assert set(DEEDS) <= set(rig.loop._cooldowns)
    assert rig.store.decisions[-1][1].kind == "wait"


async def test_failed_profile_refresh_does_not_hold_inventory(world: World) -> None:
    world.game.on_text("/inv", ("items", 3625102))
    rig = Rig(world)
    await rig.loop.step()
    assert [(r.scenario, r.status) for r in rig.store.runs] == [("refresh", "failed")]
    await world.feed("profile", 3624478)
    await rig.loop.step()
    assert world.game.payloads() == ["😎Я", "/inv"]
    assert set(rig.loop._cooldowns) == {"refresh:profile"}


async def test_cooldown_survives_run_journal_failure(world: World) -> None:
    rig = Rig(world)

    async def broken(run_id: int, status: str, reason: str, at: datetime) -> None:
        raise ConnectionError("db down")

    rig.store.run_finished = broken  # type: ignore[method-assign]
    with pytest.raises(ConnectionError):
        await rig.loop.step()
    assert set(rig.loop._cooldowns) == {"refresh:profile"}


async def test_pause_between_steps_is_not_failure(world: World) -> None:
    world.game.on_text("😎Я", ("profile", 3624478))
    world.game.on_text("/to_eat", ("food", 3521844))
    await world.feed("profile", 3624478)
    await world.feed("items", 3625102)
    await world.feed("items", 3516682)
    await world.settings.update(
        lambda s: s.model_copy(
            update={
                "features": s.features.model_copy(
                    update={"books": False, "cards_containers": False}
                )
            }
        ),
        changed_by="test",
    )
    rig = Rig(world)
    await set_engine(world, paused=True)
    await world.feed("food", 3521844)
    await rig.loop.step()
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("fastfood", "stopped", "paused")
    ]
    assert rig.loop._cooldowns == {}


async def test_pause_after_decision_stops_first_step(world: World) -> None:
    await world.feed("profile", 3624478)
    await world.feed("items", 3625102)
    rig = Rig(world)
    record = rig.store.record

    async def pause_then_record(at: datetime, decision: Decision) -> int:
        await set_engine(world, paused=True)
        return await record(at, decision)

    rig.store.record = pause_then_record  # type: ignore[method-assign]
    await rig.loop.step()
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("book", "stopped", "paused")
    ]
    assert world.game.payloads() == [] and rig.loop._cooldowns == {}
    assert rig.notes.codes == []


async def test_uncertified_step_stays_suppressed_after_switch_to_live(
    dry_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Многошаговых несертифицированных сценариев не осталось: сон здесь — как несертифицированный.
    from app.engine.scenarios import library

    monkeypatch.setitem(
        loop_module.SCENARIOS, "sleep", ScenarioSpec("sleep", library.sleep, False)
    )
    # Сон здесь по дедлайну (через 58 мин.), от часов не зависит.
    await dry_world.settings.update(
        lambda s: s.model_copy(update={"features": s.features.model_copy(update={"sleep": True})}),
        changed_by="test",
    )
    dry_world.game.on_text("🛌Спать", ("sleep", 3526861))
    await dry_world.feed("profile", 3610633)
    send_text = dry_world.game.send_text

    async def switch_after_menu(chat_id: int, text: str, reply_to: int | None = None) -> int:
        sent = await send_text(chat_id, text, reply_to)
        if text == "🛌Спать":
            await set_engine(dry_world, mode="live")
        return sent

    dry_world.game.send_text = switch_after_menu  # type: ignore[method-assign]
    rig = Rig(dry_world)
    await rig.loop.step()
    # Запуск начат в dry_run и остаётся в нём; причина dry_run важнее uncertified, как в шлюзе.
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("sleep", "suppressed", "dry_run")
    ]
    assert dry_world.game.payloads() == ["🛌Спать"]


async def test_memory_store_closes_running_runs() -> None:
    store = MemoryPlannerStore()
    at = moment()
    await store.run_started(1, "book", {}, at)
    done = await store.run_started(1, "card", {}, at)
    await store.run_finished(done, "done", "card_used", at)
    assert await store.close_running(at + timedelta(minutes=1)) == 1
    assert [(r.status, r.reason) for r in store.runs] == [
        ("interrupted", "restart"),
        ("done", "card_used"),
    ]
    # Прерванный рестартом запуск мог исполниться — он тоже «последний».
    assert await store.last_done() == {"book": at, "card": at}


async def test_last_done_loaded_from_store_and_updated(world: World) -> None:
    rig = Rig(world)
    earlier = rig.clock.now() - timedelta(hours=2)
    run = await rig.store.run_started(1, "tangerine", {}, earlier)
    await rig.store.run_finished(run, "done", "no_error", earlier)
    world.game.on_text("😎Я", ("profile", 3624478))
    await rig.loop.step()
    assert [(r.scenario, r.status) for r in rig.store.runs][-1] == ("refresh", "done")
    assert rig.loop._last_done is not None
    assert rig.loop._last_done["tangerine"] == earlier
    assert set(rig.loop._last_done) == {"tangerine", "refresh"}


async def test_not_playing_recipient_notified(world: World) -> None:
    only_tangerine = {
        name: name == "tangerine" for name in type(world.settings.current.features).model_fields
    }
    await world.settings.update(
        lambda s: s.model_copy(update={"features": s.features.model_copy(update=only_tangerine)}),
        changed_by="test",
    )
    world.game.on_text("/gt", ("tangerine", 3599304))
    await world.feed("profile", 3624478)
    rig = Rig(world)
    await rig.loop.step()
    assert [(r.scenario, r.status, r.reason) for r in rig.store.runs] == [
        ("tangerine", "refused", "not_player")
    ]
    assert rig.notes.codes == ["tangerine_not_player"]


class FixedClock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return time.monotonic()


MSK = timezone(timedelta(hours=3))
NOON = datetime(2026, 9, 27, 12, 0, tzinfo=MSK)


def capture_decide(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, int]]:
    seen: list[dict[str, int]] = []

    def fake(*args: Any, done_today: dict[str, int], **kwargs: Any) -> Decision:
        seen.append(dict(done_today))
        return Wait(None, "no_timers")

    monkeypatch.setattr(loop_module, "decide", fake)
    return seen


async def test_deeds_done_today_loaded_per_day_and_counted(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryPlannerStore()
    for scenario, status, started in (
        ("deed:harvest", "done", NOON - timedelta(hours=2)),
        ("deed:harvest", "done", NOON - timedelta(hours=12, seconds=1)),
        ("deed:dconv", "done", NOON - timedelta(hours=1)),
        ("deed:dconv", "interrupted", NOON - timedelta(minutes=30)),
    ):
        run = await store.run_started(1, scenario, {}, started)
        await store.run_finished(run, status, "", started)
    seen = capture_decide(monkeypatch)
    rig = Rig(world, store=store)
    clock = FixedClock(NOON)
    rig.loop._clock = clock
    await rig.loop.step()
    assert seen[-1] == {"deed:harvest": 1, "deed:dconv": 1}
    dconv = Act("deed:dconv", {}, "focus dconv (1 today)")
    await rig.loop._after(dconv, ScenarioResult("done", "activity_started"), NOON, NOON)
    await rig.loop._after(dconv, ScenarioResult("refused", "busy"), NOON, NOON)
    # Запуск, начатый вчера и законченный сегодня, относится ко вчера.
    yesterday = NOON - timedelta(days=1)
    await rig.loop._after(dconv, ScenarioResult("done", "activity_started"), yesterday, NOON)
    await rig.loop.step()
    assert seen[-1] == {"deed:harvest": 1, "deed:dconv": 2}
    # Смена дня в 00:00 MSK: счётчик заново из хранилища.
    clock.at = NOON + timedelta(hours=12, seconds=1)
    await rig.loop.step()
    assert seen[-1] == {}


async def test_deeds_done_today_store_failure_is_retried(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryPlannerStore()
    run = await store.run_started(1, "deed:harvest", {}, NOON)
    await store.run_finished(run, "done", "", NOON)
    loaded = store.done_on_day

    async def broken(day: date) -> dict[str, int]:
        raise ConnectionError("db down")

    store.done_on_day = broken  # type: ignore[method-assign]
    seen = capture_decide(monkeypatch)
    rig = Rig(world, store=store)
    rig.loop._clock = FixedClock(NOON)
    await rig.loop.step()
    assert seen[-1] == {}
    store.done_on_day = loaded  # type: ignore[method-assign]
    await rig.loop.step()
    assert seen[-1] == {"deed:harvest": 1}


async def test_metro_run_saved_and_durations_loaded(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = {"duration_s": 960.0, "steps": 162, "outcome": "finished"}

    async def fake_metro(ctx: Any, state: Any, params: Any) -> ScenarioResult:
        return ScenarioResult("done", "finished", {"metro": record})

    monkeypatch.setitem(loop_module.SCENARIOS, "metro", ScenarioSpec("metro", fake_metro, True))
    store = MemoryMetroRunStore()
    await store.save(None, "done", {"duration_s": 1500.0})
    rig = Rig(world)
    rig.loop._metro_store = store
    await rig.loop.step()
    assert rig.loop._metro_durations == [1500.0]
    decision = await rig.store.record(datetime.now(UTC), Act("metro", {}, "metro_ready"))
    await rig.loop._execute(Act("metro", {}, "metro_ready"), decision, dry_run=False)
    assert store.runs[-1] == {**record, "scenario_run_id": len(rig.store.runs), "status": "done"}
    assert rig.loop._metro_durations == [1500.0, 960.0]
    for _ in range(25):
        await rig.loop._execute(Act("metro", {}, "metro_ready"), decision, dry_run=False)
    assert rig.loop._metro_durations == [960.0] * 20


async def test_paused_metro_run_is_saved(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    # Приостановленный забег продолжится позже, но его запись (карта, путь) нужна и сейчас.
    record = {"duration_s": 300.0, "steps": 40, "outcome": "paused"}

    async def fake_metro(ctx: Any, state: Any, params: Any) -> ScenarioResult:
        return ScenarioResult("stopped", "paused", {"metro": record})

    monkeypatch.setitem(loop_module.SCENARIOS, "metro", ScenarioSpec("metro", fake_metro, True))
    store = MemoryMetroRunStore()
    rig = Rig(world)
    rig.loop._metro_store = store
    decision = await rig.store.record(datetime.now(UTC), Act("metro", {}, "metro_ready"))
    await rig.loop._execute(Act("metro", {}, "metro_ready"), decision, dry_run=False)
    assert [(r.status, r.reason) for r in rig.store.runs] == [("stopped", "paused")]
    assert store.runs == [{**record, "scenario_run_id": 1, "status": "stopped"}]


async def test_run_started_in_dry_run_stays_simulated_after_switch(dry_world: World) -> None:
    world = dry_world
    world.game.on_text("/to_eat", ("food", 3521844))
    world.game.on_text("🌭Хот-дог", ("food", 3624983))

    async def to_live(delivery: Delivery) -> None:
        # Переключение в live, пока сертифицированный сценарий между шагами.
        if any(isinstance(e, FoodMenu) for e in delivery.events):
            await set_engine(world, mode="live")

    world.bus.subscribe(to_live)
    rig = Rig(world)
    await rig.loop._execute(Act("fastfood", {"food": "hotdog"}, "test"), 1, dry_run=True)
    assert world.settings.current.engine.mode == "live"
    run = rig.store.runs[-1]
    assert (run.status, run.reason) == ("suppressed", "dry_run")
    assert world.game.payloads() == ["/to_eat"]
    # Следующий запуск стартует уже в новом режиме: отложенное подавлением снимается.
    await rig.loop.step()
    assert rig.loop._held == {}


async def test_manual_run_uses_manual_source(world: World) -> None:
    world.game.on_text("/read_exp", ("items", 3516680))
    rig = Rig(world)
    # Параметр, противоречащий реестру, — ошибка, а не молчаливая подмена.
    with pytest.raises(FixedParams):
        await rig.loop.request("book", {"item": "card"}, key="m0", by="admin")
    run_id, created = await rig.loop.request("book", {"item": "book"}, key="m1", by="admin")
    assert created and rig.store.runs[0].status == "queued"
    assert rig.store.runs[0].requested == {"item": "book"}
    assert await rig.loop.request("book", {}, key="m1", by="admin") == (run_id, False)
    await rig.loop.run_manual()
    assert world.game.payloads() == ["/read_exp"]
    run = rig.store.runs[run_id - 1]
    assert (run.status, run.decision_id, run.requested_by) == ("done", None, "admin")
    assert [r.req.source for r in world.store.rows.values()] == [Source.MANUAL]
    assert rig.store.decisions == []
    with pytest.raises(KeyError):
        await rig.loop.request("nope", {}, key="m2", by="admin")


async def test_manual_uncertified_is_simulated_and_not_held(world: World) -> None:
    rig = Rig(world)
    run_id, _ = await rig.loop.request("deed:rob", {}, key="u1", by="admin")
    await rig.loop.run_manual()
    run = rig.store.runs[run_id - 1]
    assert (run.status, run.reason) == ("suppressed", "uncertified")
    assert world.game.payloads() == [] and rig.loop._held == {}


async def test_manual_run_respects_manual_while_paused(world: World) -> None:
    world.game.on_text("/read_exp", ("items", 3516680))
    await set_engine(world, paused=True)
    rig = Rig(world)
    await rig.loop.request("book", {}, key="p1", by="admin")
    await rig.loop.run_manual()
    assert rig.store.runs[0].status == "done"
    await set_engine(world, manual_while_paused=False)
    await rig.loop.request("book", {}, key="p2", by="admin")
    await rig.loop.run_manual()
    assert (rig.store.runs[1].status, rig.store.runs[1].reason) == ("stopped", "paused")
    assert world.game.payloads() == ["/read_exp"]


async def test_loop_without_auto_runs_only_manual(world: World) -> None:
    script_day(world)
    rig = Rig(world, auto=False)
    task = asyncio.create_task(rig.loop.run())
    try:
        await asyncio.sleep(0.05)
        assert world.game.payloads() == []
        await rig.loop.request("refresh", {"source": "profile"}, key="r1", by="admin")
        await until(lambda: rig.store.runs[0].status == "done")
        assert world.game.payloads() == ["😎Я"] and rig.store.decisions == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_manual_run_started_in_dry_run_stays_simulated(dry_world: World) -> None:
    world = dry_world
    world.game.on_text("/to_eat", ("food", 3521844))
    world.game.on_text("🌭Хот-дог", ("food", 3624983))

    async def to_live(delivery: Delivery) -> None:
        if any(isinstance(e, FoodMenu) for e in delivery.events):
            await set_engine(world, mode="live")

    world.bus.subscribe(to_live)
    rig = Rig(world)
    run_id, _ = await rig.loop.request("fastfood", {"food": "hotdog"}, key="d1", by="admin")
    await rig.loop.run_manual()
    run = rig.store.runs[run_id - 1]
    assert (run.status, run.reason) == ("suppressed", "dry_run")
    assert world.game.payloads() == ["/to_eat"]


@pytest.mark.parametrize(
    ("scenario", "params"),
    [
        ("refresh", {}),
        ("refresh", {"source": "bank"}),
        ("fastfood", {}),
        ("fastfood", {"food": "caviar"}),
        ("sleep", {"hours": 3}),
        ("sleep", {"hours": "8"}),
        ("battle_target", {"target": "/job"}),
        ("stocks_dump", {"keep": 100}),
        ("stocks_dump", {"keep": 100, "margin": True}),
        ("bulls_join", {"code": "/job"}),
        ("tangerine", {"chat": -100}),
        ("smoothie", {"recipe": "🍋🍋"}),
    ],
)
async def test_manual_run_needs_required_params(
    world: World, scenario: str, params: dict[str, Any]
) -> None:
    rig = Rig(world)
    with pytest.raises(InvalidParams):
        await rig.loop.request(scenario, params, key="k", by="admin")
    assert rig.store.runs == []


@pytest.mark.parametrize(
    ("scenario", "params"),
    [
        ("refresh", {"source": "gifts"}),
        ("fastfood", {"food": "banana"}),
        ("sleep", {"hours": 12}),
        ("battle_target", {"target": "🤖Hooli"}),
        ("stocks_dump", {"keep": 100, "margin": 5}),
        ("bulls_join", {"code": "join_fight_AbCdEfGhIjK"}),
        ("tangerine", {"chat": -100, "reply_to": 7}),
        ("smoothie", {"recipe": "🍋🍇🍏🥕🍅"}),
        ("metro", {}),
        ("deed:job", {}),
    ],
)
async def test_manual_run_with_required_params_is_queued(
    world: World, scenario: str, params: dict[str, Any]
) -> None:
    rig = Rig(world)
    _, created = await rig.loop.request(scenario, params, key="k", by="admin")
    assert created and rig.store.runs[0].status == "queued"


class BrokenNotes(Notes):
    async def notify(self, level: Level, code: str, text: str) -> None:
        raise ConnectionError("db down")


class BeginFails(MemoryPlannerStore):
    async def run_begin(self, run_id: int, at: datetime) -> None:
        raise ConnectionError("db down")


async def _next_manual_done(rig: Rig, key: str) -> None:
    rig.world.game.on_text("😎Я", ("profile", 3624478))
    run_id, _ = await rig.loop.request("refresh", {"source": "profile"}, key=key, by="admin")
    await until(lambda: rig.store.runs[run_id - 1].status == "done")


async def test_manual_run_closed_when_after_fails(world: World) -> None:
    # Нет ответа на /read_exp: сценарий неудачен, а уведомление о неудаче падает (БД).
    rig = Rig(world, auto=False, notes=BrokenNotes())
    task = asyncio.create_task(rig.loop.run())
    try:
        run_id, _ = await rig.loop.request("book", {}, key="a1", by="admin")
        await until(lambda: rig.store.runs[run_id - 1].finished_at is not None, 3.0)
        assert rig.store.runs[run_id - 1].status == "failed"
        await _next_manual_done(rig, "a2")
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_manual_run_failed_when_begin_not_stored(world: World) -> None:
    rig = Rig(world, auto=False, store=BeginFails())
    task = asyncio.create_task(rig.loop.run())
    try:
        run_id, _ = await rig.loop.request("book", {}, key="b1", by="admin")
        await until(lambda: rig.store.runs[run_id - 1].status != "queued")
        run = rig.store.runs[run_id - 1]
        assert (run.status, run.reason) == ("failed", "store_failed")
        assert world.game.payloads() == [] and not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
