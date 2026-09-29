from datetime import UTC, datetime

from app.engine.events import AntiFlood, Event, Unrecognized
from app.engine.parsing import Parser, default_parser
from app.engine.settings import ChatsSection
from app.engine.types import IncomingMessage

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
GAME = 227859379
FLOOD = "Ты шлёшь запросы к боту слишком часто. Полегче, йоу."


def _msg(text: str | None, chat: int = GAME, *, outgoing: bool = False) -> IncomingMessage:
    return IncomingMessage(chat, 1, 0, "new", NOW, NOW, text, outgoing=outgoing)


def test_antiflood_recognized() -> None:
    events = default_parser().parse(_msg(FLOOD))
    assert events == [AntiFlood()]
    assert events[0].to_json() == {"kind": "antiflood"}


def test_other_text_no_events_without_chats() -> None:
    assert default_parser().parse(_msg("совсем непонятный текст")) == []


def test_failing_recognizer_isolated() -> None:
    def broken(msg: IncomingMessage) -> list[Event]:
        raise ValueError("boom")

    def ok(msg: IncomingMessage) -> list[Event]:
        return [AntiFlood()]

    assert Parser([broken, ok]).parse(_msg("x")) == [AntiFlood()]


def test_unrecognized_only_for_game_chat() -> None:
    parser = default_parser(ChatsSection())
    assert parser.parse(_msg("совсем непонятное\nвторая строка")) == [
        Unrecognized(first_line="совсем непонятное")
    ]
    assert parser.parse(_msg(FLOOD)) == [AntiFlood()]
    assert parser.parse(_msg("чужой чат", chat=-100)) == []


def test_outgoing_and_empty_not_parsed() -> None:
    parser = default_parser(ChatsSection())
    assert parser.parse(_msg(FLOOD, outgoing=True)) == []
    assert parser.parse(_msg(None)) == []
    assert parser.parse(_msg("")) == []


def test_outgoing_forward_in_shared_team_and_invite_chat_not_parsed() -> None:
    # Чат команды совпадает с чатом приглашений: пересылка бота (исходящее) не должна дать событий,
    # иначе итог задания разобрался бы повторно и задвоил «Итоги».
    team = -1001149209877
    parser = default_parser(ChatsSection(bulls_invite_chat_id=team, team_chat_id=team))
    forwarded = "Ты завершил задание в команде и заработал 90🏆."
    assert parser.parse(_msg(forwarded, chat=team, outgoing=True)) == []
    # И входящее сообщение с тем же текстом: в этом чате читаются только приглашения.
    assert parser.parse(_msg(forwarded, chat=team)) == []
