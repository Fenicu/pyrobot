from datetime import UTC, datetime

from app.engine.types import Button, IncomingMessage

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _msg(text: str | None, inline: tuple[Button, ...] = ()) -> IncomingMessage:
    return IncomingMessage(
        chat_id=1,
        msg_id=2,
        revision=0,
        kind="new",
        date=NOW,
        received_at=NOW,
        text=text,
        inline=inline,
    )


def test_button_lookup() -> None:
    m = _msg("x", (Button("Начать", 0, 0, data="maze_start"),))
    assert m.button("maze_start") is not None
    assert m.button("maze_up") is None


def test_content_hash_stable_and_sensitive() -> None:
    a = _msg("карта", (Button("⬆️", 0, 1, data="maze_up"),))
    b = _msg("карта", (Button("⬆️", 0, 1, data="maze_up"),))
    c = _msg("карта2", (Button("⬆️", 0, 1, data="maze_up"),))
    assert a.content_hash() == b.content_hash()
    assert a.content_hash() != c.content_hash()


def test_markup_json_shapes() -> None:
    assert _msg("x").markup_json() is None
    inline = _msg("x", (Button("A", 0, 0, data="a"),)).markup_json()
    assert inline == {"inline": [["A", 0, 0, "a", None, None]]}
    reply = IncomingMessage(
        chat_id=1,
        msg_id=2,
        revision=0,
        kind="new",
        date=NOW,
        received_at=NOW,
        text="x",
        reply_kb=(("😎Я", "⚔Битва"),),
    )
    assert reply.markup_json() == {"reply": [["😎Я", "⚔Битва"]]}
