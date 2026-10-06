from datetime import UTC, datetime, timedelta

from app.engine.bus import Delivery
from app.engine.gateway.types import ActionStatus, Match, Verdict
from app.engine.parsing.refusals import Busy
from app.engine.scenarios.context import ScenarioContext, Step, expect_edit
from app.engine.settings import Settings
from app.engine.state.model import CharacterState
from tests.engine.fakegame import GAME, World
from tests.engine.helpers import make_msg
from tests.engine.scenarios.conftest import context


def _never(delivery: Delivery) -> Match | None:
    return None


async def test_state_and_settings_default_to_empty(world: World) -> None:
    ctx = ScenarioContext(world.gateway, game_chat_id=GAME, simulate=False, paused=lambda: False)
    assert ctx.state() == CharacterState()
    assert ctx.settings() == Settings()
    assert ctx.task_id is None


async def test_state_and_settings_are_read_fresh(world: World) -> None:
    ctx = context(world)
    assert ctx.state().money is None
    await world.feed("profile", 3624478)
    assert ctx.state().money is not None
    assert not ctx.settings().features.gadgets_buy
    features = world.settings.current.features.model_copy(update={"gadgets_buy": True})
    await world.settings.update(
        lambda s: s.model_copy(update={"features": features}), changed_by="test"
    )
    assert ctx.settings().features.gadgets_buy


async def test_send_and_click_pass_deadline_and_task_id(world: World) -> None:
    ctx = context(world, scenario="gadget_upgrade", task_id=7)
    past = datetime.now(UTC) - timedelta(seconds=1)
    sent = await ctx.send("😎Я", _never, deadline=past)
    clicked = await ctx.click(5, "cancel_inline", _never, deadline=past)
    assert (sent.step, sent.reason) == (Step.FAILED, "deadline")
    assert (clicked.step, clicked.reason) == (Step.FAILED, "deadline")
    rows = list(world.store.rows.values())
    assert [(r.status, r.req.deadline, r.req.task_id) for r in rows] == [
        (ActionStatus.REJECTED, past, 7),
        (ActionStatus.REJECTED, past, 7),
    ]
    # Без срока клик доходит до проверки кнопки — значит, срок выше пришёл из аргумента.
    later = await ctx.click(5, "cancel_inline", _never)
    assert later.reason == "stale_button"
    assert world.store.rows[3].req.deadline is None


def test_expect_edit_matches_only_its_message() -> None:
    predicate = expect_edit(42, Busy)
    busy = (Busy(left_s=60),)
    other = Delivery(make_msg("Ты занят", msg_id=41, kind="edit"), busy, 0, 1)
    own = Delivery(make_msg("Ты занят", msg_id=42, kind="edit"), busy, 0, 2)
    silent = Delivery(make_msg("Апгрейд", msg_id=42, kind="edit"), (), 0, 3)
    assert predicate(other) is None
    assert predicate(silent) is None
    assert predicate(own) == Match(Verdict.CONFIRMED, Busy.kind)
    refusing = expect_edit(42)
    assert refusing(other) is None
    assert refusing(own) == Match(Verdict.REFUSED, Busy.kind)
