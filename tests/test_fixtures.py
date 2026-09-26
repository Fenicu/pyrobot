from datetime import UTC, datetime

from tests.fixtures import game, game_msg

FAMILIES = (
    "profile",
    "battle",
    "activities",
    "refusals",
    "sleep",
    "food",
    "items",
    "gorbushka",
    "levelup",
    "crew",
    "bulls",
    "stocks",
    "smoothie",
    "tangerine",
    "screens",
    "swinfo",
)


def test_every_family_has_messages() -> None:
    for family in FAMILIES:
        assert game(family), family


def test_new_message_converted() -> None:
    msg = game_msg("profile", 3610633)
    assert msg.chat_id == 227859379
    assert msg.kind == "new" and msg.revision == 0
    assert msg.date == datetime(2026, 8, 15, 0, 21, 45, tzinfo=UTC)
    assert msg.text is not None and msg.text.startswith("Битва через 6ч. 38 мин.!")
    assert msg.reply_kb[0] == ("😎Я", "⚔Битва", "🏢Офис")


def test_edit_and_inline_converted() -> None:
    msg = game_msg("gorbushka", 3516738)
    assert msg.kind == "edit"
    assert msg.date == datetime(2026, 1, 3, 2, 12, 21, tzinfo=UTC)
    assert msg.revision == int(msg.date.timestamp())
    button = msg.button("gorbushka_fight")
    assert button is not None and button.text == "⚔Сразиться"
    assert msg.origin == datetime(2026, 1, 3, 2, 12, 7, tzinfo=UTC)


def test_sender_kept_for_other_chats() -> None:
    post = game_msg("swinfo", 3817108)
    assert (post.chat_id, post.from_id) == (-1001109615116, 376592453)
    assert game_msg("profile", 3610633).from_id == 227859379
