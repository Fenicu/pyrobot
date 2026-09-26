from datetime import UTC, datetime

from app.engine.events import AntiFlood, Event
from app.engine.parsing import Parser, default_parser
from app.engine.types import IncomingMessage

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _msg(text: str) -> IncomingMessage:
    return IncomingMessage(1, 1, 0, "new", NOW, NOW, text)


def test_antiflood_recognized() -> None:
    events = default_parser().parse(_msg("Ты шлёшь запросы к боту слишком часто. Полегче, йоу."))
    assert events == [AntiFlood()]
    assert events[0].to_json() == {"kind": "antiflood"}


def test_other_text_no_events() -> None:
    assert default_parser().parse(_msg("Офис ☣️Black Mesa")) == []


def test_failing_recognizer_isolated() -> None:
    def broken(msg: IncomingMessage) -> list[Event]:
        raise ValueError("boom")

    def ok(msg: IncomingMessage) -> list[Event]:
        return [AntiFlood()]

    assert Parser([broken, ok]).parse(_msg("x")) == [AntiFlood()]
