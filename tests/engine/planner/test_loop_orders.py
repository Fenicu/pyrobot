import asyncio
from datetime import datetime

from app.engine.battle_orders import BattleOrder
from app.engine.planner.loop import ORDERS_TTL_S, PlannerLoop
from app.engine.planner.store import MemoryPlannerStore
from app.engine.planner.types import Act
from app.engine.settings import StaticSettings
from app.engine.state.model import CharacterState
from tests.engine.planner.test_decide import BASE, NOW, awake

# Битва из `awake()` - через 3 часа от NOW; аккаунт - Black Mesa.
BATTLE = datetime(2026, 9, 26, 13, 0, tzinfo=NOW.tzinfo)
HOOLI = BattleOrder(BATTLE, "all", "🤖Hooli")
UMBRELLA = BattleOrder(BATTLE, "bmesa", "☂️Umbrella")


class Clock:
    def __init__(self) -> None:
        self.mono = 1000.0

    def now(self) -> datetime:
        return NOW

    def monotonic(self) -> float:
        return self.mono


class Source:
    """Источник приказов теста: чтения считаются, сбой и ожидание задаёт тест. Чтение с `gate`
    видит набор на момент старта, а отдаёт его после отпускания."""

    def __init__(self, *orders: BattleOrder) -> None:
        self.orders: tuple[BattleOrder, ...] = orders
        self.reads = 0
        self.fail = False
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def active(self, now: datetime) -> tuple[BattleOrder, ...]:
        self.reads += 1
        seen = self.orders
        if self.gate is not None:
            self.started.set()
            await self.gate.wait()
        if self.fail:
            raise ConnectionError("db down")
        return seen


class Notes:
    async def notify(self, level: str, code: str, text: str) -> None:
        pass


class Rig:
    def __init__(self, source: Source | None, state: CharacterState | None = None) -> None:
        self.clock = Clock()
        self.acts: list[Act] = []
        self.loop = PlannerLoop(
            gateway=object(),  # type: ignore[arg-type]
            state=lambda: state or awake(company="bmesa"),
            settings=StaticSettings(BASE),
            clock=self.clock,
            store=MemoryPlannerStore(),
            notifier=Notes(),
            ready=lambda: None,
            battle_orders=source,
        )

        async def execute(act: Act, decision_id: int, *, dry_run: bool) -> None:
            self.acts.append(act)

        self.loop._execute = execute  # type: ignore[method-assign]

    def targets(self) -> list[str]:
        return [a.params["target"] for a in self.acts if a.scenario == "battle_target"]


async def test_first_step_reads_orders_and_applies_them() -> None:
    source = Source(HOOLI)
    rig = Rig(source)
    await rig.loop.step()
    assert source.reads == 1
    assert rig.targets() == ["🤖Hooli"]


async def test_step_without_source_ignores_orders() -> None:
    rig = Rig(None)
    await rig.loop.step()
    assert rig.targets() == []


async def test_cache_lives_ttl_then_rereads() -> None:
    source = Source(HOOLI)
    rig = Rig(source)
    await rig.loop.step()
    await rig.loop.step()
    assert source.reads == 1
    rig.clock.mono += ORDERS_TTL_S
    await rig.loop.step()
    assert source.reads == 2


async def test_read_started_before_orders_changed_is_discarded() -> None:
    source = Source()
    source.gate = asyncio.Event()
    rig = Rig(source)
    pending = asyncio.create_task(rig.loop.step())
    await source.started.wait()
    # Приказ записан и движок разбужен, пока чтение ещё шло: его результат устарел.
    source.orders = (HOOLI,)
    rig.loop.orders_changed()
    source.gate.set()
    await pending
    assert rig.targets() == []
    assert rig.loop._orders is None and rig.loop._orders_dirty
    source.gate = None
    await rig.loop.step()
    assert source.reads == 2
    assert rig.targets() == ["🤖Hooli"]
    assert not rig.loop._orders_dirty


async def test_failed_read_with_stale_cache_keeps_old_orders() -> None:
    source = Source(HOOLI)
    rig = Rig(source)
    await rig.loop.step()
    rig.clock.mono += ORDERS_TTL_S + 1
    source.fail = True
    await rig.loop.step()
    assert source.reads == 2
    assert rig.targets() == ["🤖Hooli", "🤖Hooli"]


async def test_failed_read_while_dirty_waits_and_does_not_send_cancelled_order() -> None:
    source = Source(HOOLI)
    rig = Rig(source)
    await rig.loop.step()
    assert rig.targets() == ["🤖Hooli"]
    # Приказ отменён, но подтверждённое изменение прочитать не удалось.
    source.orders = ()
    source.fail = True
    rig.loop.orders_changed()
    await rig.loop.step()
    assert rig.targets() == ["🤖Hooli"]
    assert rig.loop._orders_dirty
    # База вернулась: отмена прочитана, цель из настроек уже выставлена - действий нет.
    source.fail = False
    await rig.loop.step()
    assert rig.targets() == ["🤖Hooli"]


async def test_first_read_failure_without_cache_is_unknown() -> None:
    source = Source(HOOLI)
    source.fail = True
    rig = Rig(source)
    await rig.loop.step()
    assert rig.targets() == []
    source.fail = False
    await rig.loop.step()
    assert rig.targets() == ["🤖Hooli"]


async def test_orders_changed_wakes_loop_and_bumps_revision() -> None:
    rig = Rig(Source())
    revision = rig.loop.revision
    rig.loop._wake.clear()
    rig.loop.orders_changed()
    assert rig.loop._wake.is_set()
    assert rig.loop.revision == revision + 1


async def test_orders_changed_rereads_before_ttl() -> None:
    source = Source(HOOLI)
    rig = Rig(source)
    await rig.loop.step()
    source.orders = (UMBRELLA,)
    rig.loop.orders_changed()
    await rig.loop.step()
    assert source.reads == 2
    assert rig.targets() == ["🤖Hooli", "☂️Umbrella"]


async def test_plan_reads_orders_without_filling_the_cache() -> None:
    source = Source(HOOLI)
    rig = Rig(source)
    view = await rig.loop.outlook()
    assert view.outlook.hints.battle_target == "🤖Hooli"
    assert view.outlook.hints.battle_source == "order_all"
    assert rig.loop._orders is None


async def test_plan_with_dirty_failed_read_is_unknown() -> None:
    source = Source(HOOLI)
    rig = Rig(source)
    await rig.loop.step()
    source.fail = True
    rig.loop.orders_changed()
    view = await rig.loop.outlook()
    verdicts = {c.scenario: c.verdict for c in view.outlook.considered}
    assert verdicts["battle_target"] == "orders_unknown"
    assert view.outlook.hints.battle_source == "default"
    assert "orders_unknown" in {w.kind for w in view.outlook.wakeups}
