from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from app.engine.parsing import default_parser
from app.engine.settings import ChatsSection
from app.engine.state.reducer import StateReducer
from app.engine.types import IncomingMessage
from tests.fixtures import game_msg

T0 = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)
PARSER = default_parser(ChatsSection())


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def fixture_at(
    family: str, msg_id: int, minutes: float, created: float | None = None
) -> IncomingMessage:
    return replace(
        game_msg(family, msg_id),
        date=at(minutes),
        created_at=at(minutes if created is None else created),
    )


def feed(
    reducer: StateReducer,
    state: dict[str, Any],
    family: str,
    msg_id: int,
    minutes: float,
    created: float | None = None,
) -> dict[str, Any]:
    msg = fixture_at(family, msg_id, minutes, created)
    return reducer.apply(state, msg, PARSER.parse(msg))


def value(state: dict[str, Any], name: str) -> Any:
    field = state.get(name)
    return None if field is None else field["value"]
