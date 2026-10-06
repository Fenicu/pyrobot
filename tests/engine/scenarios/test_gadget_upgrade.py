"""Сертификация заточки гаджета на живых текстах 06.10 (`gadget_texts`, кадры 15–27)."""

import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.engine.clock import Clock
from app.engine.scenarios.context import ScenarioContext
from app.engine.scenarios.gadgets import gadget_upgrade
from app.engine.scenarios.library import run_scenario
from app.engine.settings import GadgetUpgradeSection, Settings
from app.engine.state.model import CharacterState, Obs
from app.engine.types import Button, IncomingMessage
from tests.engine.fakegame import GAME, LIVE, World, running_world
from tests.engine.gadget_texts import (
    CONFIRM_2,
    CONFIRM_3,
    CONFIRM_BUTTONS,
    FAIL_0,
    FAIL_0_DOT,
    OK_1,
    OK_2,
    OK_3,
    UP_BUTTONS,
    UP_BUTTONS_AUTO,
    UP_RIGHT_0,
    UP_RIGHT_2,
    UPGRADES,
    UPGRADES_CONFIRM_ON,
    UPGRADES_P1,
    game_text,
)
from tests.engine.scenarios.certify import certifies

PHONE = "Китайская мобила"
TASK = GadgetUpgradeSection(
    status="active",
    task_id=3,
    slot="right",
    gadget=PHONE,
    kind="white",
    target=25,
    start_level=0,
    started_at=datetime(2026, 10, 6, 14, 0, tzinfo=UTC),
)
UPGRADING = LIVE.model_copy(update={"gadget_upgrade": TASK})
# Итог 4-го уровня — по образцу `OK_3` (кадр 25) с уровнем на один выше.
OK_4 = f"⚪️4\xa0📱{PHONE}\n\nПрименил ⚪️\n\n💪Успех! 4⚪️ (+12%).\n\nУспех: 5⚪️\nПровал: 4⚪️."


def swap(text: str, old: str, new: str) -> str:
    """Производный кадр: замена обязана сработать, иначе тест молча проверял бы живой кадр."""
    assert old in text
    return text.replace(old, new)


UPGRADES_L2 = swap(UPGRADES_CONFIRM_ON, f"⚪️3\xa0📱{PHONE}", f"⚪️2\xa0📱{PHONE}")
StateFn = Callable[[], CharacterState]
SettingsFn = Callable[[], Settings]


@pytest.fixture
async def upgrading() -> AsyncIterator[World]:
    async for w in running_world(UPGRADING):
        yield w


def ref(text: str, buttons: tuple[Button, ...]) -> IncomingMessage:
    """Шаблон правки для `FakeGame`: текст и разметка, остальное подставит эмулятор."""
    return game_text(text, buttons=buttons)


class After:
    """Часы: `before` до первого клика `payload`, `after` — после него."""

    def __init__(self, world: World, payload: str, before: datetime, after: datetime) -> None:
        self.world, self.payload, self.before, self.after = world, payload, before, after

    def now(self) -> datetime:
        return self.after if self.payload in self.world.game.payloads() else self.before

    def monotonic(self) -> float:
        return time.monotonic()


def context(
    world: World,
    *,
    state: StateFn | None = None,
    settings: SettingsFn | None = None,
    clock: Clock | None = None,
) -> ScenarioContext:
    return ScenarioContext(
        world.gateway,
        game_chat_id=GAME,
        simulate=False,
        paused=lambda: False,
        timeout_s=0.3,
        clock=clock,
        scenario="gadget_upgrade",
        state=state or (lambda: world.state),
        settings=settings or (lambda: world.settings.current),
        task_id=3,
    )


async def run(
    world: World,
    *,
    state: StateFn | None = None,
    settings: SettingsFn | None = None,
    clock: Clock | None = None,
    **params: Any,
) -> tuple[str, str, dict[str, Any]]:
    ctx = context(world, state=state, settings=settings, clock=clock)
    base = {"task_id": 3, "slot": "right", "gadget": PHONE, "white_until": 7, "until": None}
    result = await run_scenario(gadget_upgrade, ctx, CharacterState(), {**base, **params})
    assert world.gateway.lease is None
    return result.status, result.reason, result.details or {}


def screens(world: World, upgrades: str = UPGRADES_P1, up: str = UP_RIGHT_0) -> None:
    world.game.on_text("/upgrades", game_text(upgrades))
    world.game.on_text("/up_right", game_text(up, buttons=UP_BUTTONS_AUTO))


def clicks(world: World, *texts: str) -> None:
    """Правки сообщения `/up_right` по кликам `up_right_low`: каждый клик берёт следующую."""
    for text in texts:
        world.game.on_click("up_right_low", edit=ref(text, UP_BUTTONS))


def counts(d: dict[str, Any]) -> tuple[Any, ...]:
    return d["task_id"], d["attempts"], d["ok"], d["fail"], d["level"]


@certifies("gadget_upgrade")
async def test_batch_without_confirm(upgrading: World) -> None:
    screens(upgrading)
    clicks(upgrading, FAIL_0, FAIL_0_DOT, OK_1, OK_2)
    status, reason, d = await run(upgrading, target=2, kind="white", batch=20)
    assert (status, reason) == ("done", "target_reached")
    assert counts(d) == (3, 4, 2, 2, 2)
    assert d["spent"] == {"white": 4, "blue": 0, "red": 0}
    assert upgrading.game.payloads() == ["/upgrades", "/up_right", *["up_right_low"] * 4]
    # Каждый клик — по сообщению `/up_right` и с ревизией его последней правки.
    sent = [s for s in upgrading.game.sent if s.kind == "click"]
    assert len({s.message_id for s in sent}) == 1


@certifies("gadget_upgrade")
async def test_batch_end_is_done(upgrading: World) -> None:
    screens(upgrading)
    clicks(upgrading, FAIL_0, FAIL_0_DOT)
    status, reason, d = await run(upgrading, target=5, kind="auto", batch=2)
    assert (status, reason) == ("done", "batch")
    assert counts(d) == (3, 2, 0, 2, 0)
    assert upgrading.game.payloads() == ["/upgrades", "/up_right", "up_right_low", "up_right_low"]


@certifies("gadget_upgrade")
async def test_confirm_mode_accepts_each_attempt(upgrading: World) -> None:
    screens(upgrading, UPGRADES_L2, UP_RIGHT_2)
    for confirm, result in ((CONFIRM_2, OK_3), (CONFIRM_3, OK_4)):
        upgrading.game.on_click("up_right_low", edit=ref(confirm, CONFIRM_BUTTONS))
        upgrading.game.on_click("up_right_low_1_accept", edit=ref(result, UP_BUTTONS))
    status, reason, d = await run(upgrading, target=4, kind="white", batch=20)
    assert (status, reason) == ("done", "target_reached")
    assert counts(d) == (3, 2, 2, 0, 4)
    assert d["spent"] == {"white": 2, "blue": 0, "red": 0}
    assert upgrading.game.payloads() == [
        "/upgrades",
        "/up_right",
        *["up_right_low", "up_right_low_1_accept"] * 2,
    ]


@certifies("gadget_upgrade")
async def test_target_reached_by_screen(upgrading: World) -> None:
    screens(upgrading, UPGRADES_CONFIRM_ON)
    status, reason, d = await run(upgrading, target=3, kind="white", batch=20)
    assert (status, reason, d["level"], d["attempts"]) == ("done", "target_reached", 3, 0)
    assert upgrading.game.payloads() == ["/upgrades"]


@certifies("gadget_upgrade")
async def test_exhausted_by_screen(upgrading: World) -> None:
    empty = swap(UPGRADES_P1, "⚪️ простые: 10340\xa0шт.", "⚪️ простые: 0\xa0шт.")
    screens(upgrading, empty)
    status, reason, d = await run(upgrading, target=5, kind="white", batch=20)
    assert (status, reason) == ("done", "exhausted")
    assert d["level"] == 0 and d["attempts"] == 0
    seen = datetime.fromisoformat(d["exhausted_seen_at"])
    assert abs(seen - datetime.now(UTC)) < timedelta(minutes=1)
    assert upgrading.game.payloads() == ["/upgrades"]


@certifies("gadget_upgrade")
async def test_exhausted_mid_batch(upgrading: World) -> None:
    last = swap(UP_RIGHT_0, "⚪️ 10340\xa0шт.", "⚪️ 1\xa0шт.")
    screens(upgrading, up=last)
    clicks(upgrading, FAIL_0)
    status, reason, d = await run(upgrading, target=5, kind="white", batch=20)
    assert (status, reason) == ("done", "exhausted")
    assert counts(d) == (3, 1, 0, 1, 0)
    assert "exhausted_seen_at" not in d
    assert upgrading.game.payloads() == ["/upgrades", "/up_right", "up_right_low"]


@certifies("gadget_upgrade")
async def test_gadget_changed_on_screen(upgrading: World) -> None:
    screens(upgrading, UPGRADES)
    status, reason, d = await run(upgrading, target=5, kind="white", batch=20)
    assert (status, reason, d["task_id"], d["attempts"]) == ("nothing", "gadget_changed", 3, 0)
    assert upgrading.game.payloads() == ["/upgrades"]


@certifies("gadget_upgrade")
async def test_gadget_changed_mid_batch_stops_clicks(upgrading: World) -> None:
    screens(upgrading)
    other = swap(OK_1, f"📱{PHONE}", "📱Hooli phone")
    clicks(upgrading, FAIL_0, other)
    status, reason, d = await run(upgrading, target=5, kind="white", batch=20)
    assert (status, reason) == ("nothing", "gadget_changed")
    # Улучшение потрачено, но не на гаджет задачи: в траты, не в успехи и уровень.
    assert counts(d) == (3, 2, 0, 1, 0)
    assert d["spent"] == {"white": 2, "blue": 0, "red": 0}
    assert upgrading.game.payloads() == ["/upgrades", "/up_right", "up_right_low", "up_right_low"]


@certifies("gadget_upgrade")
async def test_task_id_changed_mid_batch(upgrading: World) -> None:
    screens(upgrading)
    clicks(upgrading, FAIL_0, FAIL_0_DOT)
    restarted = UPGRADING.model_copy(
        update={"gadget_upgrade": TASK.model_copy(update={"task_id": 4})}
    )

    def settings() -> Settings:
        clicked = "up_right_low" in upgrading.game.payloads()
        return restarted if clicked else upgrading.settings.current

    status, reason, d = await run(upgrading, settings=settings, target=5, kind="white", batch=20)
    assert (status, reason, d["attempts"]) == ("nothing", "task_changed", 1)
    assert upgrading.game.payloads() == ["/upgrades", "/up_right", "up_right_low"]


@certifies("gadget_upgrade")
async def test_task_stopped_before_confirm_accept(upgrading: World) -> None:
    screens(upgrading, UPGRADES_L2, UP_RIGHT_2)
    upgrading.game.on_click("up_right_low", edit=ref(CONFIRM_2, CONFIRM_BUTTONS))
    stopped = UPGRADING.model_copy(
        update={"gadget_upgrade": TASK.model_copy(update={"status": "stopped"})}
    )

    def settings() -> Settings:
        clicked = "up_right_low" in upgrading.game.payloads()
        return stopped if clicked else upgrading.settings.current

    status, reason, d = await run(upgrading, settings=settings, target=5, kind="white", batch=20)
    assert (status, reason, d["attempts"]) == ("nothing", "task_changed", 0)
    assert upgrading.game.payloads() == ["/upgrades", "/up_right", "up_right_low"]


@certifies("gadget_upgrade")
async def test_battle_window_starts_mid_batch(upgrading: World) -> None:
    screens(upgrading)
    clicks(upgrading, FAIL_0, FAIL_0_DOT)
    now = datetime.now(UTC)
    battle = (now + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    seen = Obs(value=battle, at=battle - timedelta(hours=1))

    def state() -> CharacterState:
        return upgrading.state.model_copy(update={"battle_at": seen})

    clock = After(upgrading, "up_right_low", now, battle - timedelta(minutes=5))
    status, reason, d = await run(
        upgrading, state=state, clock=clock, target=5, kind="white", batch=20
    )
    assert (status, reason, d["attempts"]) == ("nothing", "battle_window", 1)
    assert upgrading.game.payloads() == ["/upgrades", "/up_right", "up_right_low"]


@certifies("gadget_upgrade")
async def test_attempt_from_other_message_not_accepted(upgrading: World) -> None:
    screens(upgrading)
    upgrading.game.on_click("up_right_low", new=(ref(OK_1, UP_BUTTONS),))
    status, reason, d = await run(upgrading, target=5, kind="white", batch=20)
    assert (status, reason) == ("failed", "timeout")
    assert d["attempts"] == 0
    assert upgrading.game.payloads() == ["/upgrades", "/up_right", "up_right_low"]


@certifies("gadget_upgrade")
async def test_unclear_click_outcome_is_failed(upgrading: World) -> None:
    screens(upgrading)
    status, reason, d = await run(upgrading, target=5, kind="white", batch=20)
    assert (status, reason) == ("failed", "timeout")
    assert (d["task_id"], d["attempts"], d["level"]) == (3, 0, 0)
    assert upgrading.game.payloads() == ["/upgrades", "/up_right", "up_right_low"]


@certifies("gadget_upgrade")
async def test_until_passed_sends_no_click(upgrading: World) -> None:
    screens(upgrading)
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    status, reason, _ = await run(upgrading, target=5, kind="white", batch=20, until=past)
    assert (status, reason) == ("failed", "deadline")
    assert upgrading.game.payloads() == ["/upgrades", "/up_right"]


@certifies("gadget_upgrade")
async def test_other_gadget_on_confirm_sends_no_accept(upgrading: World) -> None:
    screens(upgrading, UPGRADES_L2, UP_RIGHT_2)
    other = swap(CONFIRM_2, f"📱{PHONE}", "📱Hooli phone")
    upgrading.game.on_click("up_right_low", edit=ref(other, CONFIRM_BUTTONS))
    upgrading.game.on_click("up_right_low_1_accept", edit=ref(OK_3, UP_BUTTONS))
    status, reason, d = await run(upgrading, target=5, kind="white", batch=20)
    assert (status, reason, d["attempts"]) == ("nothing", "gadget_changed", 0)
    assert upgrading.game.payloads() == ["/upgrades", "/up_right", "up_right_low"]


@certifies("gadget_upgrade")
async def test_gateway_task_change_is_nothing(upgrading: World) -> None:
    # Задачу перезапустили, а сценарий ещё видит старую запись: клик отклоняет шлюз.
    screens(upgrading)
    clicks(upgrading, FAIL_0)
    restarted = TASK.model_copy(update={"task_id": 4})
    await upgrading.settings.update(
        lambda s: s.model_copy(update={"gadget_upgrade": restarted}), changed_by="test"
    )
    status, reason, d = await run(
        upgrading, settings=lambda: UPGRADING, target=5, kind="white", batch=20
    )
    assert (status, reason, d["attempts"]) == ("nothing", "task_changed", 0)
    assert upgrading.game.payloads() == ["/upgrades", "/up_right"]


class Calls:
    """Часы: `first` на первом чтении, дальше `then`."""

    def __init__(self, first: datetime, then: datetime) -> None:
        self.first, self.then, self.calls = first, then, 0

    def now(self) -> datetime:
        self.calls += 1
        return self.first if self.calls == 1 else self.then

    def monotonic(self) -> float:
        return time.monotonic()


@certifies("gadget_upgrade")
async def test_gateway_deadline_in_guard_window_is_nothing(upgrading: World) -> None:
    # Клик отклонён по `deadline`, и к этому моменту наступило окно битвы: итог — его вердикт.
    screens(upgrading)
    now = datetime.now(UTC)
    battle = (now + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    seen = Obs(value=battle, at=battle - timedelta(hours=1))

    def state() -> CharacterState:
        return upgrading.state.model_copy(update={"battle_at": seen})

    past = (now - timedelta(seconds=1)).isoformat()
    clock = Calls(now, battle - timedelta(minutes=5))
    status, reason, _ = await run(
        upgrading, state=state, clock=clock, target=5, kind="white", batch=20, until=past
    )
    assert (status, reason) == ("nothing", "battle_window")
    assert upgrading.game.payloads() == ["/upgrades", "/up_right"]
