import asyncio
import random
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import app.engine.scenarios.metro as metro_module
from app.engine.events import Event
from app.engine.memory import MemoryJournal
from app.engine.metro.solver import policy_of
from app.engine.parsing.metro import (
    MetroBuffs,
    MetroChest,
    MetroChestOpened,
    MetroEarlyExit,
    MetroExit,
    MetroFight,
    MetroFirstAid,
    MetroLoot,
    MetroMap,
    MetroNpc,
    recognize_metro,
)
from app.engine.scenarios.context import ScenarioContext
from app.engine.scenarios.library import ScenarioResult, run_scenario
from app.engine.scenarios.metro import metro
from app.engine.settings import MetroSection
from app.engine.state.model import CharacterState
from app.engine.types import Button, IncomingMessage
from tests.engine.fakegame import GAME, World, running_world
from tests.engine.metro.sim import hide_events, tree_maze
from tests.engine.metro.simgame import RUN, SimGame, enter_with_real_frames
from tests.engine.parsing.test_metro import EARLY_FINISHED, LOST_FIGHT
from tests.engine.scenarios.certify import certifies
from tests.fixtures import game_msg, game_versions

RUN2 = 3625352
EVENTS = {run: [recognize_metro(m)[0] for m in game_versions("metro", run)] for run in (RUN, RUN2)}
ENTRY = ["🏢Офис", "🚇Метро", "maze_enter_accept"] + [
    f"maze_buf_tokens_{b}" for b in ("fastMove", "strong", "firstAid")
]


class Notes:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.texts: list[str] = []

    async def notify(self, level: Any, code: str, text: str) -> None:
        self.sent.append((level, code))
        self.texts.append(text)


def _click_for(event: Event, nxt: Event) -> str:
    if isinstance(event, (MetroLoot, MetroFight, MetroChestOpened)):
        return "maze_continue"
    if isinstance(event, MetroNpc):
        verdict = "accept" if isinstance(nxt, MetroFight) else "decline"
        return f"maze_npc_{event.strength}_{verdict}"
    if isinstance(event, MetroChest):
        return "maze_chest_accept" if isinstance(nxt, MetroChestOpened) else "maze_chest_decline"
    if isinstance(event, MetroMap) and isinstance(nxt, MetroFirstAid):
        return "maze_first_aid"
    if isinstance(event, MetroFirstAid):
        return "maze_first_aid_accept"
    if isinstance(event, MetroExit | MetroEarlyExit):
        return "maze_exit_decline" if isinstance(nxt, MetroMap) else "maze_exit_accept"
    raise AssertionError(f"no recorded click after {event}")


def recorded(first: int, last: int, run: int = RUN) -> list[tuple[str, tuple[int, ...]]]:
    """Клики живого забега между версиями `first` и `last` и правки, пришедшие в ответ."""
    events = EVENTS[run]
    out: list[tuple[str, tuple[int, ...]]] = []
    n = first
    while n < last:
        nxt = events[n + 1]
        if isinstance(nxt, MetroMap) and nxt.footer == "going":
            out.append((f"maze_{nxt.direction}", (n + 1, n + 2)))
            n += 2
        else:
            out.append((_click_for(events[n], nxt), (n + 1,)))
            n += 1
    assert n == last
    return out


def replay(world: World, start: int, last: int, run: int = RUN) -> list[str]:
    """Игра отвечает кадрами живого забега: старт — версией `start`, дальше — как в записи.
    Вход и бафы — всегда кадры первого забега."""
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", run, start))
    clicks = recorded(start, last, run)
    for data, versions in clicks:
        world.game.on_click(data, edits=tuple(("metro", run, v) for v in versions))
    return [*ENTRY, "maze_start", *(data for data, _ in clicks)]


def start_at(world: World, run: int, version: int) -> None:
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", run, version))


class Live:
    """Приёмник живых кадров; часы `monotonic` идут на 1.5 с за вызов — троттлинг не мешает
    видеть каждый кадр."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.ticks = 0.0

    def __call__(self, type_: str, data: dict[str, Any]) -> None:
        assert type_ == "metro_live"
        self.frames.append(data)

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        self.ticks += 1.5
        return self.ticks


def ctx(
    world: World,
    stop_after: int | None = None,
    notes: Notes | None = None,
    live: Live | None = None,
) -> ScenarioContext:
    # Пауза после заданного числа отправок останавливает сценарий в безопасной точке.
    journal = world.pipeline._journal
    assert isinstance(journal, MemoryJournal)

    async def history(chat_id: int, msg_id: int) -> list[IncomingMessage]:
        return await journal.revisions(chat_id, msg_id)

    async def reread(chat_id: int, msg_id: int) -> IncomingMessage | None:
        # Как в рантайме: текущая версия из «Telegram» через конвейер, она же — текущая ревизия.
        msg = await world.game.fetch(chat_id, msg_id)
        if msg is not None:
            await world.pipeline.process(msg)
            world.pipeline.prime(msg)
        return msg

    return ScenarioContext(
        world.gateway,
        game_chat_id=GAME,
        simulate=False,
        paused=lambda: stop_after is not None and len(world.game.sent) >= stop_after,
        timeout_s=0.3,
        notifier=notes,
        history=history,
        reread=reread,
        clock=live,
        publish=live,
        run_id=41 if live is not None else None,
    )


async def run(world: World, context: ScenarioContext, **params: Any) -> ScenarioResult:
    return await run_scenario(metro, context, CharacterState(), params)


@certifies("metro")
async def test_enter_buys_token_buffs_and_starts(world: World) -> None:
    expected = replay(world, 5, 5)
    result = await run(world, ctx(world, stop_after=len(expected)))
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == expected
    assert not [p for p in world.game.payloads() if p.startswith("maze_buf_coins")]
    assert result.details is not None
    record = result.details["metro"]
    assert record["buffs"] == ["fastMove", "strong", "firstAid"]
    assert record["vitals"][0] == {"step": 0, "pos": [0, 0], "stamina": 88, "packs": 7}
    assert world.gateway.lease is None


@certifies("metro")
async def test_cooldown_refused_at_entrance(world: World) -> None:
    world.game.on_text("🏢Офис", ("screens", 3623175))
    world.game.on_text("🚇Метро", ("metro", 3624531))
    result = await run(world, ctx(world))
    assert (result.status, result.reason) == ("refused", "metro_cooldown")
    assert world.game.payloads() == ["🏢Офис", "🚇Метро"]
    assert world.state.metro_ready_at is not None


@certifies("metro")
async def test_explores_like_recorded_run(world: World) -> None:
    """Ходы, лут, бои с NPC, аптечки, сундук со стрелой, тайником и гранатой — кадры живого
    забега; решатель выбирает те же клики, что и запись, до развилки на версии 260."""
    expected = replay(world, 7, 260)
    result = await run(world, ctx(world, stop_after=len(expected)))
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == expected
    assert result.details is not None
    record = result.details["metro"]
    kinds = {e["kind"] for e in record["events"]}
    assert {"metro_loot", "metro_npc", "metro_fight", "metro_chest"} <= kinds
    assert {e["result"] for e in record["events"] if e["kind"] == "metro_chest_opened"} == {
        "arrow",
        "stash",
        "grenade",
    }
    assert expected.count("maze_first_aid_accept") == 3
    moves = sum(p in ("maze_up", "maze_down", "maze_left", "maze_right") for p in expected)
    # Первый кадр (версия 7) — уже приход вправо: решатель засчитывает и этот шаг.
    assert record["steps"] == moves + 1
    assert world.state.stamina is not None and world.state.stamina.value == 100


def _summed(events: list[dict[str, Any]]) -> dict[str, int]:
    found: dict[str, int] = {}
    for e in events:
        loot = {e["item"]: e["amount"]} if e["kind"] == "metro_loot" else e.get("loot", {})
        if e["kind"] in ("metro_loot", "metro_fight", "metro_chest_opened"):
            for item, amount in loot.items():
                found[item] = found.get(item, 0) + amount
    return found


@certifies("metro")
async def test_live_frames_follow_the_run(world: World) -> None:
    expected = replay(world, 7, 260)
    live = Live()
    result = await run(world, ctx(world, stop_after=len(expected), live=live))
    assert (result.status, result.reason) == ("stopped", "paused")
    assert result.details is not None
    record = result.details["metro"]
    frames = live.frames
    # Кадр на старте обхода и после каждого обработанного экрана, последний — при остановке.
    assert len(frames) >= len(expected) - len(ENTRY)
    assert all(f["running"] for f in frames[:-1]) and frames[0]["steps"] == 0
    steps = [f["steps"] for f in frames]
    assert steps == sorted(steps) and len(set(steps)) > 100
    assert len({tuple(f["pos"]) for f in frames}) > 50
    last = frames[-1]
    assert (last["running"], last["outcome"]) == (False, "paused")
    assert (last["steps"], last["pos"], last["path"]) == (
        record["steps"],
        record["pos"],
        record["path"],
    )
    assert last["message_id"] == record["message_id"] and last["scenario_run_id"] == 41
    assert last["started_at"] == record["started_at"]
    assert last["events"] == record["events"][-30:] and last["last_event"] == record["events"][-1]
    assert last["found"] == _summed(record["events"]) and last["found"]["money"] > 0
    growing = [f["found"].get("money", 0) for f in frames]
    assert growing == sorted(growing)


@certifies("metro")
async def test_live_frame_on_cancel_is_cancelled_not_failure(world: World) -> None:
    # Рестарт или деплой посреди забега отменяет задачу: последний кадр — «cancelled», не сбой.
    replay(world, 7, 260)
    walking = asyncio.Event()

    class Watched(Live):
        def __call__(self, type_: str, data: dict[str, Any]) -> None:
            super().__call__(type_, data)
            if len(self.frames) >= 5:
                walking.set()

    live = Watched()
    task = asyncio.create_task(run(world, ctx(world, live=live)))
    async with asyncio.timeout(5):
        await walking.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    last = live.frames[-1]
    assert (last["running"], last["outcome"]) == (False, "cancelled")
    assert all(f["running"] for f in live.frames[:-1])


@certifies("metro")
async def test_live_frame_at_finish_takes_found_from_exit_screen(world: World) -> None:
    replay(world, 529, 532)
    live = Live()
    battle = datetime.now(UTC) + timedelta(minutes=25)
    context = ctx(world, live=live)
    result = await run(world, context, battle_at=battle.isoformat(), margin_min=25)
    assert (result.status, result.reason) == ("done", "finished")
    assert result.details is not None
    record = result.details["metro"]
    last = live.frames[-1]
    assert (last["running"], last["outcome"]) == (False, "finished")
    [shown] = [e for e in record["events"] if e["kind"] == "metro_exit"]
    assert last["found"] == shown["found"] and last["found"]["money"] == 157
    assert last["battle_at"] == battle.isoformat()
    assert last["budget"]["total_s"] is not None and last["budget"]["used"] == 1.0
    assert last["mode"] == "leave" and last["leave_reason"] == "deadline"


@certifies("metro")
async def test_exit_declined_while_cells_remain(world: World) -> None:
    expected = replay(world, 283, 288)
    assert "maze_exit_decline" in expected
    result = await run(world, ctx(world, stop_after=len(expected)))
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == expected


@certifies("metro")
async def test_leaves_by_deadline_and_collects(world: World) -> None:
    expected = replay(world, 529, 532)
    assert expected[-1] == "maze_exit_accept"
    notes = Notes()
    battle = datetime.now(UTC) + timedelta(minutes=25)
    context = ctx(world, notes=notes)
    result = await run(world, context, battle_at=battle.isoformat(), margin_min=25)
    assert (result.status, result.reason) == ("done", "finished")
    assert world.game.payloads() == expected
    assert result.details is not None
    record = result.details["metro"]
    assert record["leave_reason"] == "deadline" and record["result"]["money"] == 157
    assert world.state.metro_ready_at is not None
    assert world.state.metro_ready_at.src == "derived"


@certifies("metro")
async def test_unknown_screen_stops_run(world: World) -> None:
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    world.game.on_click("maze_left", edits=(("metro", RUN, 6), ("screens", 3623175)))
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("stopped", "unexpected_screen")
    assert world.game.payloads()[-1] == "maze_left"
    assert notes.sent == [("warn", "metro_halted")]


@certifies("metro")
async def test_lost_fight_is_continued_then_unknown_screen_halts(world: World) -> None:
    """Поражение в бою: игра молчит, пока не нажато «Продолжить»; экран после него неизвестен."""
    expected = replay(world, 7, 47)
    lost = replace(
        game_msg("metro", RUN, 48),
        text=(
            "Ты сразился с 👨Продаваном 👨Георгий (11)\n\n"
            "👎Ты сражался до последнего, но увы, сегодня не твой день - ты проиграл.\n\n"
            "🔋Осталось выносливости: 0%"
        ),
    )
    world.game.on_click("maze_npc_low_accept", edit=lost)
    world.game.on_click("maze_continue", edit=OTHER)
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("stopped", "unexpected_screen")
    assert world.game.payloads() == [*expected, "maze_npc_low_accept", "maze_continue"]
    assert notes.sent == [("warn", "metro_halted")]


def _without_packs(msg: IncomingMessage) -> IncomingMessage:
    return replace(msg, inline=tuple(b for b in msg.inline if b.data != "maze_first_aid"))


@certifies("metro")
async def test_lost_fight_without_packs_leaves_early(world: World) -> None:
    """Поражение без аптечек: «Продолжить», карта с 🔋0% — дальше не пройти: 🚪 → «Выйти»."""
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=_without_packs(game_msg("metro", RUN, 7)))
    clicks = recorded(7, 47)
    for data, versions in clicks:
        world.game.on_click(
            data, edits=tuple(_without_packs(game_msg("metro", RUN, v)) for v in versions)
        )
    stuck = _without_packs(game_msg("metro", RUN, 49))
    stuck = replace(stuck, text=(stuck.text or "").replace("🔋55%", "🔋0%"))
    finished = replace(game_msg("metro", RUN, 532), text=EARLY_FINISHED, inline=())
    world.game.on_click(
        "maze_npc_low_accept", edit=replace(game_msg("metro", RUN, 48), text=LOST_FIGHT)
    )
    world.game.on_click("maze_continue", edit=stuck)
    world.game.on_click("maze_exit", edit=("metro", RUN2, 390))
    world.game.on_click("maze_exit_accept", edit=finished)
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("done", "finished")
    assert world.game.payloads() == [
        *ENTRY,
        "maze_start",
        *(data for data, _ in clicks),
        "maze_npc_low_accept",
        "maze_continue",
        "maze_exit",
        "maze_exit_accept",
    ]
    assert notes.sent == []
    assert result.details is not None
    assert result.details["metro"]["leave_reason"] == "no_stamina"
    assert result.details["metro"]["result"] == {"burger": 1}


@certifies("metro")
async def test_move_without_new_window_runs_ladder_then_halts(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    # Только «Идёшь …» со старым окном: ход не подтверждён, игра молчит и на лестницу.
    world.game.on_click("maze_left", edit=("metro", RUN, 6))
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("stopped", "timeout")
    # Кадр — «Идёшь Вправо»: шаг назад — влево.
    assert world.game.payloads()[len(ENTRY) :] == [
        "maze_start",
        "maze_left",
        "maze_left",
        "maze_exit",
        "maze_exit",
    ]
    assert notes.sent == [("warn", "metro_halted")]
    assert result.details is not None and result.details["metro"]["outcome"] == "timeout"


def test_stuck_threshold_is_three_expected_steps_but_not_under_a_minute() -> None:
    context = ScenarioContext(
        None,  # type: ignore[arg-type]
        game_chat_id=GAME,
        simulate=False,
        paused=lambda: False,
        timeout_s=20.0,
    )
    fast = MetroBuffs(bought=("fastMove",), offers=(), tokens=0, coins=0)
    slow = MetroBuffs(bought=(), offers=(), tokens=0, coins=0)
    assert metro_module._stuck_timeout_s(context, fast) == 60.0
    assert metro_module._stuck_timeout_s(context, slow) == 240.0


OTHER = ("screens", 3623175)
MOVES = ("maze_up", "maze_down", "maze_left", "maze_right")


@certifies("metro")
async def test_unknown_screen_after_enter_halts_at_once(world: World) -> None:
    world.game.on_text("🏢Офис", OTHER)
    world.game.on_text("🚇Метро", ("metro", RUN, 0))
    # Вход оплачен, а вместо бафов — незнакомый экран: остановка сразу, не по таймауту.
    world.game.on_click("maze_enter_accept", edit=OTHER)
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("stopped", "unexpected_screen")
    assert notes.sent == [("warn", "metro_halted")]
    assert world.game.payloads() == ["🏢Офис", "🚇Метро", "maze_enter_accept"]


@certifies("metro")
async def test_unknown_screen_after_buff_halts_at_once(world: World) -> None:
    world.game.on_text("🏢Офис", OTHER)
    world.game.on_text("🚇Метро", ("metro", RUN, 0))
    world.game.on_click("maze_enter_accept", edit=("metro", RUN, 1))
    world.game.on_click("maze_buf_tokens_fastMove", edit=OTHER)
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("stopped", "unexpected_screen")
    assert notes.sent == [("warn", "metro_halted")]


@certifies("metro")
async def test_without_fast_move_no_notification(world: World) -> None:
    start_at(world, RUN, 5)
    notes = Notes()
    result = await run(world, ctx(world, stop_after=4, notes=notes), buffs=[])
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == ["🏢Офис", "🚇Метро", "maze_enter_accept", "maze_start"]
    assert not [n for n in notes.sent if n[1] == "metro_slow"]


@certifies("metro")
async def test_screen_changed_behind_safe_point_redecides(world: World) -> None:
    start_at(world, RUN, 5)
    world.game.on_click("maze_down", edits=(("metro", RUN, 8), ("metro", RUN, 9)))
    changed: list[int] = []

    def paused() -> bool:
        # На безопасной точке перед первым ходом (решение — влево) экран сменился на приход
        # вправо (ручной ход): решение пересчитывается по нему — вниз.
        sent = len(world.game.sent)
        if sent == len(ENTRY) + 1 and not changed:
            [message] = [
                i for i, m in world.game.messages.items() if (m.text or "").startswith("🔋")
            ]
            now = datetime.now(UTC)
            arrived = replace(
                game_msg("metro", RUN, 7),
                msg_id=message,
                kind="edit",
                revision=10_000,
                date=now,
                received_at=now,
            )
            world.pipeline._remember(arrived)
            changed.append(message)
        return sent >= len(ENTRY) + 2

    context = ScenarioContext(
        world.gateway, game_chat_id=GAME, simulate=False, paused=paused, timeout_s=0.3
    )
    result = await run(world, context)
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads()[len(ENTRY) :] == ["maze_start", "maze_down"]
    assert result.details is not None and result.details["metro"]["steps"] == 2


def _swap_screen(world: World, run: int, version: int) -> None:
    """Последняя правка сообщения забега в кэше конвейера — другая (ручное действие)."""
    [message] = [
        i
        for i, m in world.game.messages.items()
        if (m.text or "").startswith(
            ("🔋", "Ты у входа", "Бафы", "Ты собираешься досрочно", "Ты в шаге")
        )
    ]
    now = datetime.now(UTC)
    swapped = replace(
        game_msg("metro", run, version),
        msg_id=message,
        kind="edit",
        revision=10_000,
        date=now,
        received_at=now,
    )
    world.pipeline._remember(swapped)


def _context(world: World, paused: Any, notes: Notes | None = None) -> ScenarioContext:
    return ScenarioContext(
        world.gateway,
        game_chat_id=GAME,
        simulate=False,
        paused=paused,
        timeout_s=0.3,
        notifier=notes,
    )


@certifies("metro")
async def test_unknown_answer_to_metro_halts_at_once(world: World) -> None:
    world.game.on_text("🏢Офис", OTHER)
    send_text = world.game.send_text

    async def answer(chat_id: int, text: str, reply_to: int | None = None) -> int:
        sent = await send_text(chat_id, text, reply_to)
        if text == "🚇Метро":
            now = datetime.now(UTC)
            unknown = replace(
                game_msg("metro", RUN, 0),
                msg_id=8_000_000,
                text="Метро закрыто на ремонт.",
                inline=(),
                date=now,
                received_at=now,
                created_at=now,
            )
            await world.pipeline.process(unknown)
        return sent

    world.game.send_text = answer  # type: ignore[method-assign]
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("stopped", "unexpected_screen")
    assert notes.sent == [("warn", "metro_halted")]
    assert world.game.payloads() == ["🏢Офис", "🚇Метро"]


@certifies("metro")
async def test_entrance_changed_behind_safe_point_halts(world: World) -> None:
    enter_with_real_frames(world.game)
    notes = Notes()

    def paused() -> bool:
        # Перед «Вхожу» вход уже принят вручную: кадр другой — не кликаем.
        if len(world.game.sent) == 2:
            _swap_screen(world, RUN, 1)
        return False

    result = await run(world, _context(world, paused, notes))
    assert (result.status, result.reason) == ("stopped", "screen_changed")
    assert world.game.payloads() == ["🏢Офис", "🚇Метро"]
    assert notes.sent == [("warn", "metro_halted")]


@certifies("metro")
async def test_early_exit_dropped_when_manual_move_brings_exit_close(world: World) -> None:
    """За минуту до выброса выход не виден — решение 🚪; на безопасной точке ручной ход вправо
    открыл выход в двух клетках: решение пересчитано — обычный ход к выходу."""
    start_at(world, RUN2, 388)
    world.game.on_click("maze_right", edits=(("metro", RUN2, 394), ("metro", RUN2, 395)))
    swapped: list[bool] = []

    def paused() -> bool:
        sent = len(world.game.sent)
        if sent == len(ENTRY) + 1 and not swapped:
            _swap_screen(world, RUN2, 393)
            swapped.append(True)
        return sent >= len(ENTRY) + 2

    battle = datetime.now(UTC) + timedelta(minutes=15, seconds=50)
    context = _context(world, paused)
    result = await run(world, context, battle_at=battle.isoformat(), margin_min=25)
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads()[len(ENTRY) :] == ["maze_start", "maze_right"]
    assert result.details is not None
    record = result.details["metro"]
    assert record["exit"] is not None and record["leave_reason"] != "early_exit"


@certifies("metro")
async def test_declines_npc_and_chest_like_second_run(world: World) -> None:
    """Второй живой забег: отказ от слабого NPC («Не бьёшся») и от сундука («Не открываешь»)."""
    expected = replay(world, 5, 74, RUN2)
    assert "maze_npc_low_decline" in expected and "maze_chest_decline" in expected
    context = ctx(world, stop_after=len(expected))
    result = await run(world, context, npc_low=False, chest_min_packs=8)
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == expected
    moves = [r for r in world.store.rows.values() if r.req.data in MOVES]
    assert moves and all(r.req.expect_revision is not None for r in moves)
    assert all(r.req.expect_content is not None for r in moves)


@certifies("metro")
async def test_wall_answer_to_move_into_open_cell_halts(world: World) -> None:
    """Ход вправо в проход, видный в окне, а игра отвечает «Стена»: карта разошлась с игрой —
    остановка, а не повтор хода."""
    start_at(world, RUN2, 388)
    world.game.on_click("maze_right", edit=("metro", RUN2, 389))
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("stopped", "unexpected_wall")
    assert world.game.payloads()[len(ENTRY) :] == ["maze_start", "maze_right"]
    assert notes.sent == [("warn", "metro_halted")]
    assert result.details is not None
    record = result.details["metro"]
    # Шаг — только стартовый кадр (приход влево); «Стена» шага не добавляет.
    assert record["steps"] == 1 and "wall" in [e["kind"] for e in record["events"]]


@certifies("metro")
async def test_unrequested_early_exit_offer_declined(world: World) -> None:
    start_at(world, RUN2, 388)
    world.game.on_click("maze_right", edit=("metro", RUN2, 390))
    world.game.on_click("maze_exit_decline", edit=("metro", RUN2, 391))
    result = await run(world, ctx(world, stop_after=len(ENTRY) + 4))
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads()[len(ENTRY) :] == [
        "maze_start",
        "maze_right",
        "maze_exit_decline",
        "maze_right",
    ]


@certifies("metro")
@pytest.mark.parametrize(
    ("answer", "status"), [(("metro", RUN2, 398), "done"), (OTHER, "stopped")]
)
async def test_early_exit_before_kick(
    world: World, answer: tuple[str, int, int] | tuple[str, int], status: str
) -> None:
    """За минуту до выброса игрой выход не найден: 🚪 → «Выйти». Итог досрочного выхода живьём
    не видели — ждём обычное «Получено», незнакомый экран — остановка."""
    start_at(world, RUN2, 388)
    world.game.on_click("maze_exit", edit=("metro", RUN2, 390))
    world.game.on_click("maze_exit_accept", edit=answer)
    notes = Notes()
    battle = datetime.now(UTC) + timedelta(minutes=15, seconds=50)
    context = ctx(world, notes=notes)
    result = await run(world, context, battle_at=battle.isoformat(), margin_min=25)
    assert result.status == status
    assert world.game.payloads()[len(ENTRY) :] == ["maze_start", "maze_exit", "maze_exit_accept"]
    assert result.details is not None
    assert result.details["metro"]["leave_reason"] == "early_exit"
    if status == "stopped":
        assert result.reason == "unexpected_screen" and ("warn", "metro_halted") in notes.sent


@certifies("metro")
async def test_resume_after_restart_replays_journal(world: World) -> None:
    """До рестарта забег дошёл до версии 165: правки уже в журнале. После рестарта сценарий
    читает текущий экран из Telegram (он тот же), восстанавливает карту по журналу и продолжает
    с того же места — сундук со стрелой, аптечки, тайник и граната, как в записи."""
    for version in game_versions("metro", RUN)[: 165 + 1]:
        await world.game.show(version)
    assert world.state.metro_message is not None
    assert world.state.metro_message.value is not None
    assert world.state.metro_message.value.message_id == RUN
    clicks = recorded(165, 260)
    for data, versions in clicks:
        world.game.on_click(data, edits=tuple(("metro", RUN, v) for v in versions))
    expected = [data for data, _ in clicks]
    context = ctx(world, stop_after=len(expected))
    result = await run(world, context, resume=RUN)
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == expected
    assert result.details is not None
    record = result.details["metro"]
    # Карта и путь — продолжение записанного забега, а не новый обход с нуля.
    assert record["path"][0] == [0, 0] and record["steps"] > 100
    kinds = [e["kind"] for e in record["events"]]
    assert kinds.count("metro_chest_opened") == 3 and "metro_loot" in kinds


@certifies("metro")
async def test_resume_does_not_repeat_move_that_reached_the_game(world: World) -> None:
    """Клик хода ушёл до рестарта, игра персонажа сдвинула, а в журнал правка не попала: после
    рестарта текущий экран из Telegram — уже новый кадр; ход не повторяется, решение — по нему."""
    for version in game_versions("metro", RUN)[: 165 + 1]:
        await world.game.show(version)
    [(_, versions), *_] = recorded(165, 260)
    assert versions == (166, 167)
    world.game.now_shows(RUN, 167)
    clicks = recorded(167, 201)
    for data, versions in clicks:
        world.game.on_click(data, edits=tuple(("metro", RUN, v) for v in versions))
    expected = [data for data, _ in clicks]
    result = await run(world, ctx(world, stop_after=len(expected)), resume=RUN)
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == expected
    # Свежий кадр — сундук на новой клетке (ход дошёл до игры): прошёл конвейер, состояние
    # его учло; позицию после «Продолжить» нашло сопоставление окна по всей карте.
    assert isinstance(EVENTS[RUN][167], MetroChest)
    journal = world.pipeline._journal
    assert isinstance(journal, MemoryJournal)
    assert world.game.current[RUN] in [msg for msg, _ in journal.rows]
    assert result.details is not None
    kinds = [e["kind"] for e in result.details["metro"]["events"]]
    assert "relocated" in kinds and "metro_chest_opened" in kinds


@certifies("metro")
async def test_resume_on_unknown_or_unreadable_screen_does_not_click(world: World) -> None:
    for version in game_versions("metro", RUN)[: 165 + 1]:
        await world.game.show(version)
    world.game.now_shows_other(RUN, OTHER)
    notes = Notes()
    result = await run(world, ctx(world, notes=notes), resume=RUN)
    assert (result.status, result.reason) == ("stopped", "resume_unknown_screen")
    assert world.game.payloads() == [] and notes.sent == [("warn", "metro_halted")]
    world.game.unreadable = True
    result = await run(world, ctx(world, notes=notes), resume=RUN)
    assert (result.status, result.reason) == ("stopped", "resume_unreadable")
    assert world.game.payloads() == []


@certifies("metro")
async def test_resume_on_early_exit_offer_declines(world: World) -> None:
    """После рестарта на экране — диалог досрочного выхода (решение о нём потеряно): «Остаться»."""
    for version in game_versions("metro", RUN2)[: 389 + 1]:
        await world.game.show(version)
    world.game.now_shows(RUN2, 390)
    world.game.on_click("maze_exit_decline", edit=("metro", RUN2, 391))
    result = await run(world, ctx(world, stop_after=1), resume=RUN2)
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == ["maze_exit_decline"]
    # Карта восстановлена по журналу: выход уже известен.
    assert result.details is not None and result.details["metro"]["exit"] == [14, -2]


def _entrance_says(world: World, cost: int, motivation: int) -> None:
    """На 🚇Метро игра показывает экран входа с другой ценой или мотивацией."""
    entrance = game_msg("metro", RUN, 0)
    text = (entrance.text or "").replace("требует 2🔥", f"требует {cost}🔥")
    text = text.replace("У тебя 73🔥", f"У тебя {motivation}🔥")
    world.game.on_text("🏢Офис", OTHER)
    send_text = world.game.send_text

    async def answer(chat_id: int, sent_text: str, reply_to: int | None = None) -> int:
        sent = await send_text(chat_id, sent_text, reply_to)
        if sent_text == "🚇Метро":
            now = datetime.now(UTC)
            shown = replace(
                entrance, msg_id=8_000_001, text=text, date=now, received_at=now, created_at=now
            )
            await world.game._push(shown)
        return sent

    world.game.send_text = answer  # type: ignore[method-assign,assignment]


@certifies("metro")
@pytest.mark.parametrize(
    ("cost", "motivation", "reason", "notified"),
    [
        (3, 73, "entry_cost_changed", [("warn", "metro_entry_cost")]),
        (2, 1, "no_motivation", []),
    ],
)
async def test_entrance_declined_when_price_changed_or_motivation_short(
    world: World, cost: int, motivation: int, reason: str, notified: list[tuple[str, str]]
) -> None:
    _entrance_says(world, cost, motivation)
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("nothing", reason)
    assert world.game.payloads() == ["🏢Офис", "🚇Метро", "maze_enter_decline"]
    assert notes.sent == notified


@certifies("metro")
async def test_unknown_cell_symbol_notified(world: World) -> None:
    frame = game_msg("metro", RUN, 5)
    odd = replace(frame, text=(frame.text or "").replace("⬛️", "🟫", 1))
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=odd)
    notes = Notes()
    result = await run(world, ctx(world, stop_after=len(ENTRY) + 1, notes=notes))
    assert (result.status, result.reason) == ("stopped", "paused")
    assert notes.sent == [("warn", "metro_unknown_cell")]


def _moving_until_reread(world: World, rereads: int) -> None:
    """Текущий экран — «Идёшь Вправо» (версия 166), новое окно (сундук, 167) игра покажет после
    `rereads` прочтений."""
    world.game.now_shows(RUN, 166)
    fetch = world.game.fetch
    seen: list[int] = []

    async def moving(chat_id: int, message_id: int) -> IncomingMessage | None:
        msg = await fetch(chat_id, message_id)
        seen.append(message_id)
        if len(seen) == rereads:
            world.game.now_shows(RUN, 167)
        return msg

    world.game.fetch = moving  # type: ignore[method-assign]


@certifies("metro")
async def test_resume_while_moving_rereads_until_new_window(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После рестарта на экране «Идёшь …»: ход ещё идёт — сценарий коротко ждёт и читает
    сообщение заново, а решение принимает по новому кадру (сундук на следующей клетке)."""
    monkeypatch.setattr(metro_module, "MOVING_POLL_S", 0.01)
    for version in game_versions("metro", RUN)[: 165 + 1]:
        await world.game.show(version)
    _moving_until_reread(world, 2)
    clicks = recorded(167, 201)
    for data, versions in clicks:
        world.game.on_click(data, edits=tuple(("metro", RUN, v) for v in versions))
    expected = [data for data, _ in clicks]
    result = await run(world, ctx(world, stop_after=len(expected)), resume=RUN)
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == expected
    assert result.details is not None
    # Ход «Идёшь Вправо» учтён: сундук — на клетке, куда он вёл, позицию искать не пришлось.
    assert "relocated" not in [e["kind"] for e in result.details["metro"]["events"]]


@certifies("metro")
async def test_resume_still_moving_after_wait_stops(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "MOVING_POLL_S", 0.01)
    monkeypatch.setattr(metro_module, "MOVING_WAIT_S", 0.05)
    for version in game_versions("metro", RUN)[: 165 + 1]:
        await world.game.show(version)
    _moving_until_reread(world, 100)
    result = await run(world, ctx(world), resume=RUN)
    assert (result.status, result.reason) == ("stopped", "resume_while_moving")
    assert world.game.payloads() == []


@certifies("metro")
async def test_resume_without_history_stops(world: World) -> None:
    result = await run(world, ctx(world), resume=RUN)
    assert (result.status, result.reason) == ("stopped", "resume_without_history")
    assert world.game.payloads() == []


@pytest.fixture
async def maze_world() -> AsyncIterator[World]:
    rng = random.Random(7)
    maze = hide_events(tree_maze(7, 7, rng), rng)
    async for w in running_world(game=lambda pipeline: SimGame(pipeline, maze)):
        yield w


async def test_long_run_on_simulator(maze_world: World) -> None:
    game = maze_world.game
    assert isinstance(game, SimGame)
    result = await run(maze_world, ctx(maze_world))
    assert (result.status, result.reason) == ("done", "finished")
    assert game.sim.finished
    floor = {p for p, sym in game.sim.maze.cells.items() if sym == "."}
    assert len(floor) + 1 == len(game.sim.maze.floor())
    assert result.details is not None
    record = result.details["metro"]
    assert len(record["grid"]["visited"]) == len(game.sim.maze.floor())
    assert record["result"] == game.sim.bank
    assert maze_world.state.money is None


def test_policy_params_default_to_metro_settings() -> None:
    defaults = policy_of(MetroSection())
    assert metro_module._policy({}) == defaults
    assert metro_module._policy({"npc_low": False, "heal_at": "40"}) == replace(
        defaults, npc_low=False, heal_at=40
    )


STUCK_MAP_GOING_LEFT = (
    "🔋3%\n"
    "⬛️⬜️⬜️⬜️⬜️               \n"
    "⬛️⬜️⬛️⬛️⬛️               \n"
    "⬛️⬜️😎⬜️⬜️               \n"
    "⬛️⬜️⬛️⬛️⬛️               \n"
    "⬛️⬜️⬛️⬜️⬜️               \n"
    "Идёшь Влево."
)
STUCK_MAP_WALL_BACK = (
    "🔋3%\n"
    "⬛️⬜️⬜️⬜️⬜️               \n"
    "⬛️⬜️⬛️⬛️⬛️               \n"
    "⬛️⬜️😎⬛️⬜️               \n"
    "⬛️⬜️⬛️⬛️⬛️               \n"
    "⬛️⬜️⬛️⬜️⬜️               \n"
    "Идёшь Влево."
)
STUCK_EARLY_EXIT_TEXT = (
    "Ты собираешься досрочно покинуть метро.\n"
    "Ты потеряешь половину найденного.\n\n"
    "Найдено\n"
    "🍕Пицца: 2\n"
    "📚Знания: 5\n"
    "🔩Сырьё: 3\n"
    "💵Деньги: 98\n"
    "⚙️Детали: 7\n"
    "⚪️Улучшения: 1\n"
    "🌭Хот-дог: 4\n"
    "🍔Бургер: 1\n\n"
    "Получишь половину\n"
    "🍕Пицца: 1\n"
    "📚Знания: 3\n"
    "🔩Сырьё: 2\n"
    "💵Деньги: 49\n"
    "⚙️Детали: 4\n"
    "⚪️Улучшения: 1\n"
    "🌭Хот-дог: 2\n"
    "🍔Бургер: 1\n\n"
    "Выходишь?"
)
STUCK_FINISHED_TEXT = (
    "Ты вышел из метро досрочно. Но при этом потерял половину найденного.\n\n"
    "Получено\n"
    "🍕Пицца: 1\n"
    "📚Знания: 3\n"
    "🔩Сырьё: 2\n"
    "💵Деньги: 49\n"
    "⚙️Детали: 4\n"
    "⚪️Улучшения: 1\n"
    "🌭Хот-дог: 2\n"
    "🍔Бургер: 1\n"
    "🔋Осталось выносливости: 3%\n\n"
    "К персонажу - /main."
)
EARLY_EXIT_BUTTONS = (
    Button("👍Выйти", 0, 0, "maze_exit_accept"),
    Button("👎Остаться", 0, 1, "maze_exit_decline"),
)


@certifies("metro")
async def test_stuck_step_recovered_by_back_step(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    stuck = replace(game_msg("metro", RUN, 6), text=STUCK_MAP_GOING_LEFT)
    world.game.on_click("maze_left", edit=stuck)
    world.game.on_click("maze_right", edit=("metro", RUN, 7))
    result = await run(world, ctx(world, stop_after=len(ENTRY) + 3))
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads()[len(ENTRY) :] == ["maze_start", "maze_left", "maze_right"]
    assert result.details is not None
    assert result.details["metro"]["stuck_recovered"] == ["back_step"]


@certifies("metro")
async def test_stuck_step_back_unanswered_exit_declined_recovers(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    stuck = replace(game_msg("metro", RUN, 6), text=STUCK_MAP_GOING_LEFT)
    world.game.on_click("maze_left", edit=stuck)
    early_exit = replace(
        game_msg("metro", RUN, 6), text=STUCK_EARLY_EXIT_TEXT, inline=EARLY_EXIT_BUTTONS
    )
    world.game.on_click("maze_exit", edit=early_exit)
    world.game.on_click("maze_exit_decline", edit=("metro", RUN, 7))
    result = await run(world, ctx(world, stop_after=len(ENTRY) + 5))
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads()[len(ENTRY) :] == [
        "maze_start",
        "maze_left",
        "maze_right",
        "maze_exit",
        "maze_exit_decline",
    ]
    assert result.details is not None
    assert result.details["metro"]["stuck_recovered"] == ["exit_decline"]


@certifies("metro")
async def test_stuck_step_both_unanswered_exits_with_notification(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    stuck = replace(game_msg("metro", RUN, 6), text=STUCK_MAP_GOING_LEFT)
    world.game.on_click("maze_left", edit=stuck)
    early_exit = replace(
        game_msg("metro", RUN, 6), text=STUCK_EARLY_EXIT_TEXT, inline=EARLY_EXIT_BUTTONS
    )
    world.game.on_click("maze_exit", edit=early_exit)
    finished = replace(game_msg("metro", RUN, 6), text=STUCK_FINISHED_TEXT, inline=())
    world.game.on_click("maze_exit_accept", edit=finished)
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("done", "finished")
    assert world.game.payloads()[len(ENTRY) :] == [
        "maze_start",
        "maze_left",
        "maze_right",
        "maze_exit",
        "maze_exit_decline",
        "maze_exit_accept",
    ]
    assert ("warn", "metro_stuck_exit") in notes.sent
    assert any("back_step" in t and "exit_decline" in t for t in notes.texts)
    assert result.details is not None
    assert result.details["metro"]["stuck_recovered"] == ["stuck_exit"]


@certifies("metro")
async def test_stuck_step_unknown_answer_stops_without_exit(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    stuck = replace(game_msg("metro", RUN, 6), text=STUCK_MAP_GOING_LEFT)
    world.game.on_click("maze_left", edit=stuck)
    world.game.on_click("maze_right", edit=OTHER)
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("stopped", "stuck_unknown")
    assert world.game.payloads()[len(ENTRY) :] == ["maze_start", "maze_left", "maze_right"]
    assert notes.sent == [("warn", "metro_stuck_unknown")]


@certifies("metro")
async def test_resume_on_old_stuck_map_runs_ladder(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "MOVING_POLL_S", 0.01)
    monkeypatch.setattr(metro_module, "MOVING_WAIT_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    for version in game_versions("metro", RUN)[: 165 + 1]:
        await world.game.show(version)
    stuck = replace(
        game_msg("metro", RUN, 166),
        msg_id=RUN,
        kind="edit",
        revision=next(world.game._revisions),
        date=datetime.now(UTC) - timedelta(hours=2),
    )
    world.game.current[RUN] = stuck
    world.game.on_click("maze_left", edit=("metro", RUN, 167))
    result = await run(world, ctx(world, stop_after=1), resume=RUN)
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == ["maze_left"]
    assert result.details is not None
    assert result.details["metro"]["stuck_recovered"] == ["back_step"]


@certifies("metro")
async def test_stuck_step_wall_behind_skips_to_door_and_decline(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    stuck_wall = replace(game_msg("metro", RUN, 6), text=STUCK_MAP_WALL_BACK)
    world.game.on_click("maze_left", edit=stuck_wall)
    early_exit = replace(
        game_msg("metro", RUN, 6), text=STUCK_EARLY_EXIT_TEXT, inline=EARLY_EXIT_BUTTONS
    )
    world.game.on_click("maze_exit", edit=early_exit)
    world.game.on_click("maze_exit_decline", edit=("metro", RUN, 7))
    result = await run(world, ctx(world, stop_after=len(ENTRY) + 4))
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads()[len(ENTRY) :] == [
        "maze_start",
        "maze_left",
        "maze_exit",
        "maze_exit_decline",
    ]
    assert "maze_right" not in world.game.payloads()
    assert result.details is not None
    assert result.details["metro"]["stuck_recovered"] == ["exit_decline"]


@certifies("metro")
async def test_stuck_step_door_unanswered_on_step2_recovers_on_step3(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    stuck = replace(game_msg("metro", RUN, 6), text=STUCK_MAP_GOING_LEFT)
    world.game.on_click("maze_left", edit=stuck)
    early_exit = replace(
        game_msg("metro", RUN, 6), text=STUCK_EARLY_EXIT_TEXT, inline=EARLY_EXIT_BUTTONS
    )
    finished = replace(game_msg("metro", RUN, 6), text=STUCK_FINISHED_TEXT, inline=())
    # Ступень 2: maze_exit без ответа (таймаут)
    world.game.on_click("maze_exit")
    # Ступень 3: maze_exit ответил early_exit, затем maze_exit_accept ответил finished
    world.game.on_click("maze_exit", edit=early_exit)
    world.game.on_click("maze_exit_accept", edit=finished)
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("done", "finished")
    assert world.game.payloads()[len(ENTRY) :] == [
        "maze_start",
        "maze_left",
        "maze_right",
        "maze_exit",
        "maze_exit",
        "maze_exit_accept",
    ]
    assert ("warn", "metro_stuck_exit") in notes.sent
    assert any("back_step" in t and "exit_decline" in t for t in notes.texts)
    assert result.details is not None
    assert result.details["metro"]["stuck_recovered"] == ["stuck_exit"]


@certifies("metro")
async def test_stuck_step_late_answer_before_step2_recovers_back_step(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    stuck = replace(game_msg("metro", RUN, 6), text=STUCK_MAP_GOING_LEFT)
    world.game.on_click("maze_left", edit=stuck)
    swapped = False

    def paused() -> bool:
        nonlocal swapped
        sent = len(world.game.sent)
        # После отправки maze_right (шаг назад) на безопасной точке перед ступенью 2
        # имитируем приход запоздалого ответа с картой в кэш конвейера.
        if sent == len(ENTRY) + 3 and not swapped:
            _swap_screen(world, RUN, 7)
            swapped = True
            return False
        return sent >= len(ENTRY) + 3 and swapped

    result = await run(world, _context(world, paused))
    assert (result.status, result.reason) == ("stopped", "paused")
    # maze_exit не нажимался, расшевеливание успешно по back_step
    assert world.game.payloads()[len(ENTRY) :] == ["maze_start", "maze_left", "maze_right"]
    assert result.details is not None
    assert result.details["metro"]["stuck_recovered"] == ["back_step"]


@certifies("metro")
async def test_stuck_step_late_answer_before_step3_recovers_exit_decline(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    stuck = replace(game_msg("metro", RUN, 6), text=STUCK_MAP_GOING_LEFT)
    world.game.on_click("maze_left", edit=stuck)
    early_exit = replace(
        game_msg("metro", RUN, 6), text=STUCK_EARLY_EXIT_TEXT, inline=EARLY_EXIT_BUTTONS
    )
    world.game.on_click("maze_exit", edit=early_exit)
    # maze_exit_decline без ответа (таймаут)
    swapped = False

    def paused() -> bool:
        nonlocal swapped
        sent = len(world.game.sent)
        # После отправки maze_exit_decline на безопасной точке перед ступенью 3
        # имитируем запоздалый ответ с картой
        if sent == len(ENTRY) + 5 and not swapped:
            _swap_screen(world, RUN, 7)
            swapped = True
            return False
        return sent >= len(ENTRY) + 5 and swapped

    result = await run(world, _context(world, paused))
    assert (result.status, result.reason) == ("stopped", "paused")
    # maze_exit_accept не нажимался
    assert world.game.payloads()[len(ENTRY) :] == [
        "maze_start",
        "maze_left",
        "maze_right",
        "maze_exit",
        "maze_exit_decline",
    ]
    assert result.details is not None
    assert result.details["metro"]["stuck_recovered"] == ["exit_decline"]


@certifies("metro")
async def test_stuck_step_late_early_exit_on_step2_declines(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    stuck = replace(game_msg("metro", RUN, 6), text=STUCK_MAP_GOING_LEFT)
    world.game.on_click("maze_left", edit=stuck)
    world.game.on_click("maze_exit")
    world.game.on_click("maze_exit_decline", edit=("metro", RUN, 7))
    swapped = False

    def paused() -> bool:
        nonlocal swapped
        sent = len(world.game.sent)
        # Дверь ступени 2 без ответа в срок, экран досрочного выхода пришёл перед ступенью 3.
        if sent == len(ENTRY) + 4 and not swapped:
            [message] = [
                i for i, m in world.game.messages.items() if m.text == STUCK_MAP_GOING_LEFT
            ]
            now = datetime.now(UTC)
            late = replace(
                game_msg("metro", RUN, 6),
                text=STUCK_EARLY_EXIT_TEXT,
                inline=EARLY_EXIT_BUTTONS,
                msg_id=message,
                kind="edit",
                revision=10_000,
                date=now,
                received_at=now,
            )
            world.pipeline._remember(late)
            swapped = True
            return False
        return sent >= len(ENTRY) + 5

    notes = Notes()
    result = await run(world, _context(world, paused, notes))
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads()[len(ENTRY) :] == [
        "maze_start",
        "maze_left",
        "maze_right",
        "maze_exit",
        "maze_exit_decline",
    ]
    assert notes.sent == []
    assert result.details is not None
    assert result.details["metro"]["stuck_recovered"] == ["exit_decline"]


@certifies("metro")
async def test_stuck_step_door_failure_on_step3_halts(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    stuck = replace(game_msg("metro", RUN, 6), text=STUCK_MAP_GOING_LEFT)
    world.game.on_click("maze_left", edit=stuck)
    # Ступень 1: maze_right таймаут (нет обработчика)
    # Ступень 2: maze_exit таймаут
    world.game.on_click("maze_exit")
    # Ступень 3: maze_exit тоже таймаут
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("stopped", "timeout")
    assert world.game.payloads()[len(ENTRY) :] == [
        "maze_start",
        "maze_left",
        "maze_right",
        "maze_exit",
        "maze_exit",
    ]
    assert ("warn", "metro_halted") in notes.sent
    assert result.details is not None
    assert result.details["metro"]["outcome"] == "timeout"


@certifies("metro")
async def test_stuck_step_accept_failure_on_step3_halts(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    stuck = replace(game_msg("metro", RUN, 6), text=STUCK_MAP_GOING_LEFT)
    world.game.on_click("maze_left", edit=stuck)
    early_exit = replace(
        game_msg("metro", RUN, 6), text=STUCK_EARLY_EXIT_TEXT, inline=EARLY_EXIT_BUTTONS
    )
    # Ступень 2: maze_exit отвечает early_exit, maze_exit_decline таймаут
    world.game.on_click("maze_exit", edit=early_exit)
    # Ступень 3: maze_exit_accept таймаут (нет обработчика)
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("stopped", "timeout")
    assert world.game.payloads()[len(ENTRY) :] == [
        "maze_start",
        "maze_left",
        "maze_right",
        "maze_exit",
        "maze_exit_decline",
        "maze_exit_accept",
    ]
    assert ("warn", "metro_halted") in notes.sent
    assert result.details is not None
    assert result.details["metro"]["outcome"] == "timeout"


REFUSAL = ("refusals", 3516893)


@certifies("metro")
@pytest.mark.parametrize(
    ("refusing", "payloads"),
    [
        ("maze_right", ["maze_start", "maze_left", "maze_right"]),
        ("maze_exit", ["maze_start", "maze_left", "maze_right", "maze_exit"]),
    ],
)
async def test_stuck_step_refusal_stops_without_exit(
    world: World, monkeypatch: pytest.MonkeyPatch, refusing: str, payloads: list[str]
) -> None:
    # Отказ игры отдельным сообщением — живой, но незнакомый ответ на зависание: не выходить.
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    stuck = replace(game_msg("metro", RUN, 6), text=STUCK_MAP_GOING_LEFT)
    world.game.on_click("maze_left", edit=stuck)
    world.game.on_click(refusing, new=(REFUSAL,))
    notes = Notes()
    result = await run(world, ctx(world, notes=notes))
    assert (result.status, result.reason) == ("stopped", "stuck_unknown")
    assert world.game.payloads()[len(ENTRY) :] == payloads
    assert notes.sent == [("warn", "metro_stuck_unknown")]


@certifies("metro")
async def test_stuck_step_late_unknown_frame_stops_without_exit(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    enter_with_real_frames(world.game)
    world.game.on_click("maze_start", edit=("metro", RUN, 5))
    stuck = replace(game_msg("metro", RUN, 6), text=STUCK_MAP_GOING_LEFT)
    world.game.on_click("maze_left", edit=stuck)
    swapped = False

    def paused() -> bool:
        nonlocal swapped
        # Шаг назад без ответа в срок, а перед дверью пришла правка с незнакомым экраном.
        if len(world.game.sent) == len(ENTRY) + 3 and not swapped:
            [message] = [
                i for i, m in world.game.messages.items() if m.text == STUCK_MAP_GOING_LEFT
            ]
            now = datetime.now(UTC)
            unknown = replace(
                game_msg(*OTHER), msg_id=message, kind="edit", revision=10_000, date=now
            )
            world.pipeline._remember(unknown)
            swapped = True
        return False

    notes = Notes()
    result = await run(world, _context(world, paused, notes))
    assert (result.status, result.reason) == ("stopped", "stuck_unknown")
    assert world.game.payloads()[len(ENTRY) :] == ["maze_start", "maze_left", "maze_right"]
    assert notes.sent == [("warn", "metro_stuck_unknown")]


@certifies("metro")
async def test_pause_inside_ladder_on_resume_keeps_run_record(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metro_module, "MOVING_POLL_S", 0.01)
    monkeypatch.setattr(metro_module, "MOVING_WAIT_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_WAIT_MIN_S", 0.05)
    monkeypatch.setattr(metro_module, "STUCK_STEP_FACTOR", 1.0)
    for version in game_versions("metro", RUN)[: 165 + 1]:
        await world.game.show(version)
    stuck = replace(
        game_msg("metro", RUN, 166),
        msg_id=RUN,
        kind="edit",
        revision=next(world.game._revisions),
        date=datetime.now(UTC) - timedelta(hours=2),
    )
    world.game.current[RUN] = stuck
    # Шаг назад без ответа, пауза — перед дверью.
    result = await run(world, ctx(world, stop_after=1), resume=RUN)
    assert (result.status, result.reason) == ("stopped", "paused")
    assert world.game.payloads() == ["maze_left"]
    assert result.details is not None and result.details["metro"]["outcome"] == "paused"
