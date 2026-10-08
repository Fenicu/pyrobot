from dataclasses import replace
from typing import Any

from app.engine.state.ledger import Effect
from app.engine.state.reducer import StateReducer
from app.engine.types import IncomingMessage
from tests.engine import gift_texts as g
from tests.engine.state.helpers import PARSER, at, feed, value


def _msg(text: str, minutes: float, msg_id: int, revision: int = 0, **kw: Any) -> IncomingMessage:
    msg = g.gift_msg(text, msg_id=msg_id, revision=revision, **kw)
    created = minutes if revision == 0 else 1
    return replace(msg, date=at(minutes), created_at=at(created))


def _reduce(
    reducer: StateReducer, state: dict[str, Any], msg: IncomingMessage
) -> tuple[dict[str, Any], tuple[Effect, ...]]:
    return reducer.reduce(state, msg, PARSER.parse(msg))


def _feed(reducer: StateReducer, state: dict[str, Any], msg: IncomingMessage) -> dict[str, Any]:
    return _reduce(reducer, state, msg)[0]


def test_gifts_screen_snaps_tangerine_gifts() -> None:
    reducer = StateReducer()
    state = _feed(reducer, {}, _msg(g.GIFTS_WITH_TANGERINE_GIFTS, 1, 1))
    assert (value(state, "tangerine_gifts"), value(state, "tangerines")) == (1, 2)


def test_shop_and_purchase_edits_snap_counts() -> None:
    reducer = StateReducer()
    state = _feed(reducer, {}, _msg(g.SHOP, 1, 10, buttons=g.options(*g.SHOP_OPTIONS)))
    assert (value(state, "tangerine_gifts"), value(state, "tangerines")) == (0, 237)
    # Правка после покупки: на экране уже итог, повторно не вычитается.
    state = _feed(reducer, state, _msg(g.BOUGHT, 2, 10, revision=1))
    assert (value(state, "tangerine_gifts"), value(state, "tangerines")) == (3, 203)
    assert state["tangerines"]["src"] == "screen"
    state = _feed(reducer, state, _msg(g.BOUGHT_ALL, 3, 10, revision=2))
    assert (value(state, "tangerine_gifts"), value(state, "tangerines")) == (4, 2)


def test_opened_gift_credits_rewards_food_and_ledger() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "profile", 3624478, 0)
    state = feed(reducer, state, "food", 3624997, 1)
    state = _feed(reducer, state, _msg(g.GIFTS_WITH_TANGERINE_GIFTS, 2, 1))
    money = value(state, "money")
    stock = value(state, "food_stock")
    state, effects = _reduce(reducer, state, _msg(g.OPENED, 3, 2))
    assert value(state, "tangerine_gifts") == 0
    assert value(state, "money") == money + 73
    food = value(state, "food_stock")
    assert food["hotdog"]["count"] == stock["hotdog"]["count"] + 2
    assert food["pizza"]["count"] == stock["pizza"]["count"] + 1
    assert food["burger"]["count"] == stock["burger"]["count"] + 1
    assert food["banana"]["count"] == stock["banana"]["count"]
    assert effects == (Effect("tangerine_gift", {"money": 73, "upgrades_white": 2}),)


def test_opened_gift_applied_once_per_message() -> None:
    reducer = StateReducer()
    state = _feed(reducer, {}, _msg(g.GIFTS_WITH_TANGERINE_GIFTS, 1, 1))
    state = _feed(reducer, state, _msg(g.OPENED, 2, 2))
    again, effects = _reduce(reducer, state, _msg(g.OPENED, 2, 2))
    assert (value(again, "tangerine_gifts"), effects) == (0, ())
