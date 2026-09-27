"""Защита от ограбления на эмуляторе игры: тревога из поиска, итог драки из выгрузки."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.engine.bus import Delivery
from app.engine.gateway.gateway import RECONCILE_REASON
from app.engine.gateway.types import ActionStatus, Source
from app.engine.memory import MemoryJournal
from app.engine.notify import Level
from app.engine.parsing.sleep import RobberyAlert
from app.engine.reactions import RobberyDefense, wake_key
from app.engine.scenarios.library import run_scenario, sleep
from app.engine.scenarios.obligations import factory_signup
from app.engine.settings import Settings
from app.engine.state.model import CharacterState
from app.engine.types import IncomingMessage
from tests.engine.fakegame import GAME, LIVE, World, running_world
from tests.engine.helpers import until
from tests.engine.scenarios.conftest import context
from tests.fixtures import game_msg

ALERT = ("sleep", 3420238)
BUTTON = "rob_awake_1106993"
WON = ("sleep", 3621947)
SLEEP = 3625590


class Notes:
    def __init__(self) -> None:
        self.codes: list[str] = []

    async def notify(self, level: Level, code: str, text: str) -> None:
        self.codes.append(code)


class Rig:
    def __init__(self, world: World) -> None:
        self.world = world
        self.notes = Notes()
        self.rereads = 0

        async def reread(chat_id: int, msg_id: int) -> IncomingMessage | None:
            # Как в рантайме: версия из «Telegram» через конвейер, она же — текущая ревизия.
            self.rereads += 1
            msg = await world.game.fetch(chat_id, msg_id)
            if msg is not None:
                await world.pipeline.process(msg)
                world.pipeline.prime(msg)
            return msg

        self.defense = RobberyDefense(
            gateway=world.gateway,
            settings=world.settings,
            reread=reread,
            notifier=self.notes,
            timeout_s=0.3,
        )
        world.bus.subscribe(self.defense.on_delivery, priority=20)

    async def alert(
        self, *, age: timedelta = timedelta(0), recovered: bool = False, fought: bool = False
    ) -> int:
        received = datetime.now(UTC)
        moment = received - age
        msg = replace(
            game_msg(*ALERT),
            date=moment,
            created_at=moment,
            received_at=received,
            recovered=recovered,
        )
        if fought:
            # С телефона уже прокликали: в Telegram сообщение — итог драки.
            self.world.game.messages[msg.msg_id] = msg
            self.world.game.now_shows_other(msg.msg_id, WON)
        await self.world.game.show(msg)
        return msg.msg_id

    def clicks(self) -> list[str]:
        return [s.payload for s in self.world.game.sent if s.kind == "click"]

    async def settled(self) -> None:
        await until(lambda: self.defense._queue.empty() and not self.defense._queued, 3.0)
        await self.world.game.settle()


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    async for world in running_world():
        rig = Rig(world)
        task = asyncio.create_task(rig.defense.run())
        try:
            yield rig
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def asleep_under_bridge(world: World) -> None:
    await world.feed("profile", 3624478)
    await world.feed("sleep", 3541942)


async def test_alert_wakes_up_and_fight_is_applied(rig: Rig) -> None:
    world = rig.world
    await asleep_under_bridge(world)
    money = world.state.money
    assert money is not None
    world.game.on_click(BUTTON, edit=WON)
    msg_id = await rig.alert()
    await rig.settled()
    assert rig.clicks() == [BUTTON]
    [row] = world.store.rows.values()
    assert (row.status, row.req.source) == (ActionStatus.CONFIRMED, Source.URGENT)
    assert row.req.idempotency_key == f"rob_awake:{world.game.messages[msg_id].chat_id}:{msg_id}"
    state = world.state
    assert state.busy is not None and state.busy.value is None
    assert state.money is not None and state.money.value == money.value + 21
    assert state.stamina is not None and state.stamina.value == 100
    assert rig.notes.codes == []


async def test_same_alert_again_does_not_click_twice(rig: Rig) -> None:
    # Игра не ответила: исход клика неизвестен. Та же тревога ещё раз (повторная доставка) — ключ
    # реакции от чата и сообщения, второго клика нет.
    world = rig.world
    msg_id = await rig.alert()
    await rig.settled()
    [row] = world.store.rows.values()
    assert (row.status, row.reason) == (ActionStatus.OUTCOME_UNKNOWN, "timeout")
    shown = world.game.messages[msg_id]
    events = tuple(world.pipeline._parser.parse(shown))
    await rig.defense.on_delivery(
        Delivery(msg=shown, events=events, state_version=0, journal_id=0)
    )
    await rig.settled()
    assert rig.clicks() == [BUTTON] and len(world.store.rows) == 1


async def test_old_recovered_alert_is_not_reactable(rig: Rig) -> None:
    rig.world.game.on_click(BUTTON, edit=WON)
    await rig.alert(age=timedelta(minutes=15), recovered=True)
    await rig.settled()
    assert (rig.clicks(), rig.rereads) == ([], 0)


async def test_recovered_alert_is_reread_then_clicked(rig: Rig) -> None:
    world = rig.world
    world.game.on_click(BUTTON, edit=WON)
    msg_id = await rig.alert(age=timedelta(minutes=2), recovered=True)
    shown = world.game.messages[msg_id]
    await rig.settled()
    assert (rig.clicks(), rig.rereads) == ([BUTTON], 1)
    [row] = world.store.rows.values()
    assert (row.req.expect_revision, row.req.expect_content) == (
        shown.revision,
        shown.content_hash(),
    )


async def test_recovered_alert_already_fought_is_not_clicked(rig: Rig) -> None:
    world = rig.world
    await asleep_under_bridge(world)
    await rig.alert(age=timedelta(minutes=2), recovered=True, fought=True)
    await rig.settled()
    assert (rig.clicks(), rig.rereads) == ([], 1)
    state = world.state
    assert state.busy is not None and state.busy.value is None


async def test_unreadable_recovered_alert_is_not_clicked(rig: Rig) -> None:
    rig.world.game.unreadable = True
    await rig.alert(age=timedelta(minutes=2), recovered=True)
    await rig.settled()
    assert rig.clicks() == []
    assert rig.notes.codes == ["robbery_defense_failed"]


async def test_click_passes_spending_block(rig: Rig) -> None:
    world = rig.world
    world.gateway.block_spending(RECONCILE_REASON)
    world.game.on_click(BUTTON, edit=WON)
    await rig.alert()
    await rig.settled()
    assert rig.clicks() == [BUTTON]
    assert world.gateway.spending_blocked == RECONCILE_REASON


async def set_engine(world: World, **update: object) -> None:
    await world.settings.update(
        lambda s: s.model_copy(update={"engine": s.engine.model_copy(update=update)}),
        changed_by="test",
    )


async def test_paused_click_follows_urgent_while_paused(rig: Rig) -> None:
    world = rig.world
    world.game.on_click(BUTTON, edit=WON)
    await set_engine(world, paused=True)
    await rig.alert()
    await rig.settled()
    assert rig.clicks() == [BUTTON]


async def test_paused_without_urgent_is_rejected_and_notified(rig: Rig) -> None:
    world = rig.world
    await set_engine(world, paused=True, urgent_while_paused=False)
    await rig.alert()
    await rig.settled()
    assert rig.clicks() == []
    [row] = world.store.rows.values()
    assert (row.status, row.reason) == (ActionStatus.REJECTED, "paused")
    assert rig.notes.codes == ["robbery_defense_failed"]


@pytest.mark.parametrize(
    ("update", "reason"), [({"mode": "dry_run"}, "dry_run"), ({"killed": True}, "kill_switch")]
)
async def test_dry_run_and_kill_switch_suppress(
    rig: Rig, update: dict[str, object], reason: str
) -> None:
    await set_engine(rig.world, **update)
    await rig.alert()
    await rig.settled()
    assert rig.clicks() == []
    [row] = rig.world.store.rows.values()
    assert (row.status, row.reason) == (ActionStatus.SUPPRESSED, reason)


async def test_feature_off_ignores_alert(rig: Rig) -> None:
    world = rig.world
    await world.settings.update(
        lambda s: s.model_copy(
            update={"features": s.features.model_copy(update={"robbery_defense": False})}
        ),
        changed_by="test",
    )
    await rig.alert()
    await rig.settled()
    assert rig.clicks() == [] and world.store.rows == {}


async def test_subscriber_does_not_wait_for_click(rig: Rig) -> None:
    # Без ответа игры клик ждёт итога до тайм-аута, а шина — нет.
    msg = replace(game_msg(*ALERT), date=datetime.now(UTC), received_at=datetime.now(UTC))
    delivery = Delivery(
        msg=msg, events=tuple(rig.world.pipeline._parser.parse(msg)), state_version=0, journal_id=0
    )
    await asyncio.wait_for(rig.defense.on_delivery(delivery), 0.05)


async def test_click_waits_for_scenario_safe_point(rig: Rig) -> None:
    """Тревога посреди сценария проходит в его безопасной точке: после меню сна, до клика по
    часам (кнопка сообщения, к экрану не привязана)."""
    world = rig.world
    world.game.on_click(BUTTON, edit=WON)
    world.game.on_text("🛌Спать", ("sleep", SLEEP, 0))
    world.game.on_click("sleep_7", edit=("sleep", SLEEP, 1))
    world.game.on_click("sleep_Bridge", edit=("sleep", 3541942))
    push = world.game._push
    fired: list[int] = []

    async def pushed(msg: IncomingMessage) -> None:
        await push(msg)
        if (msg.text or "").startswith("Все мы рано или поздно") and not fired:
            fired.append(1)
            alert = replace(
                game_msg(*ALERT), date=datetime.now(UTC), received_at=datetime.now(UTC)
            )
            await push(alert)

    world.game._push = pushed  # type: ignore[method-assign]
    result = await run_scenario(
        sleep, context(world), CharacterState(), {"hours": 7, "hotel": False}
    )
    await rig.settled()
    assert result.status == "done"
    assert world.game.payloads() == ["🛌Спать", BUTTON, "sleep_7", "sleep_Bridge"]


async def test_click_does_not_split_factory_signup(rig: Rig) -> None:
    """Запись на фабрику — три шага без безопасной точки: тревога ждёт конца аренды."""
    world = rig.world
    world.game.on_click(BUTTON, edit=WON)
    world.game.on_text("/crew", ("crew", 3624389))
    world.game.on_text("/crew_factory", ("crew", 3624391))
    world.game.on_text("👍Записаться", ("crew", 3624393))
    push = world.game._push
    fired: list[int] = []

    async def pushed(msg: IncomingMessage) -> None:
        await push(msg)
        if (msg.text or "").startswith("О команде") and not fired:
            fired.append(1)
            alert = replace(
                game_msg(*ALERT), date=datetime.now(UTC), received_at=datetime.now(UTC)
            )
            await push(alert)

    world.game._push = pushed  # type: ignore[method-assign]
    result = await run_scenario(factory_signup, context(world), CharacterState(), {})
    await rig.settled()
    assert result.status == "done"
    assert world.game.payloads() == ["/crew", "/crew_factory", "👍Записаться", BUTTON]


class Restart:
    """Процесс, поднятый после рестарта: тревога уже в журнале, реакция стартует заново."""

    def __init__(self, world: World, ready: list[bool] | None = None) -> None:
        self.world = world
        self.rereads = 0
        journal = world.pipeline._journal
        assert isinstance(journal, MemoryJournal)
        self.ready = ready if ready is not None else [True]

        async def reread(chat_id: int, msg_id: int) -> IncomingMessage | None:
            self.rereads += 1
            msg = await world.game.fetch(chat_id, msg_id)
            if msg is not None:
                await world.pipeline.process(msg)
                world.pipeline.prime(msg)
            return msg

        async def alerts(since: datetime) -> list[IncomingMessage]:
            return await journal.messages_with_event(GAME, RobberyAlert.kind, since)

        self.defense = RobberyDefense(
            gateway=world.gateway,
            settings=world.settings,
            reread=reread,
            alerts=alerts,
            ready=lambda: self.ready[-1],
            timeout_s=0.3,
            ready_poll_s=0.01,
        )
        world.bus.subscribe(self.defense.on_delivery, priority=20)

    async def run_until_idle(self) -> None:
        task = asyncio.create_task(self.defense.run())
        try:
            await until(lambda: self.defense.recovered, 3.0)
            await until(lambda: self.defense._queue.empty() and not self.defense._queued, 3.0)
            await self.world.game.settle()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def journaled_alert(world: World, age: timedelta = timedelta(minutes=2)) -> int:
    """Тревога, записанная прошлым процессом: доставлена и в журнале, клика не было."""
    moment = datetime.now(UTC) - age
    msg = replace(game_msg(*ALERT), date=moment, created_at=moment, received_at=moment)
    await world.game.show(msg)
    return msg.msg_id


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async for w in running_world():
        yield w


async def test_alert_left_in_journal_is_clicked_after_restart(world: World) -> None:
    await journaled_alert(world)
    world.game.on_click(BUTTON, edit=WON)
    restart = Restart(world)
    await restart.run_until_idle()
    assert [s.payload for s in world.game.sent] == [BUTTON]
    assert restart.rereads == 1


async def test_alert_with_fight_in_journal_is_not_clicked(world: World) -> None:
    msg_id = await journaled_alert(world)
    fought = replace(
        game_msg(*WON), msg_id=msg_id, kind="edit", revision=10_000, date=datetime.now(UTC)
    )
    await world.game._push(fought)
    restart = Restart(world)
    await restart.run_until_idle()
    assert world.game.sent == [] and restart.rereads == 0


async def test_alert_already_fought_in_telegram_is_not_clicked(world: World) -> None:
    # В журнале итога нет, а в Telegram — уже есть (прокликали с телефона, правка не дошла).
    msg_id = await journaled_alert(world)
    world.game.now_shows_other(msg_id, WON)
    restart = Restart(world)
    await restart.run_until_idle()
    assert world.game.sent == [] and restart.rereads == 1


async def test_old_alert_in_journal_is_ignored(world: World) -> None:
    await journaled_alert(world, age=timedelta(minutes=15))
    restart = Restart(world)
    await restart.run_until_idle()
    assert world.game.sent == [] and restart.rereads == 0


async def test_recovery_waits_until_engine_ready(world: World) -> None:
    await journaled_alert(world)
    world.game.on_click(BUTTON, edit=WON)
    ready = [False]
    restart = Restart(world, ready)
    task = asyncio.create_task(restart.defense.run())
    try:
        await asyncio.sleep(0.05)
        assert restart.rereads == 0 and not restart.defense.recovered
        ready.append(True)
        await until(lambda: [s.payload for s in world.game.sent] == [BUTTON], 3.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_wake_key_is_per_message() -> None:
    msg = game_msg(*ALERT)
    assert wake_key(msg) == f"rob_awake:{msg.chat_id}:{msg.msg_id}"
    assert len(wake_key(msg)) <= 100


def test_robbery_defense_is_on_by_default() -> None:
    assert Settings().features.robbery_defense and LIVE.features.robbery_defense
