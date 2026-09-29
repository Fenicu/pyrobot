import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.fixtures import game, game_msg, game_versions
from tools import fixtures as tool

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
    "daily",
    "bulls",
    "stocks",
    "smoothie",
    "smoothie_cooking",
    "metro",
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


def test_versions_of_edited_message() -> None:
    versions = game_versions("smoothie_cooking", 3625241)
    assert [m.kind for m in versions] == ["new"] + ["edit"] * 6
    assert [m.date for m in versions] == sorted(m.date for m in versions)
    assert game_msg("smoothie_cooking", 3625241) == versions[-1]
    assert game_msg("smoothie_cooking", 3625241, 1) == versions[1]


def test_versions_within_one_second_kept_in_order() -> None:
    versions = game_versions("metro", 3624441)
    assert len(versions) == 533
    same = [m for m in versions if m.date == datetime(2026, 9, 25, 22, 14, 11, tzinfo=UTC)]
    assert [(m.text or "").rsplit("\n", 1)[-1] for m in same] == ["Вниз", "Идёшь Вниз."]


def test_tool_numbers_same_second_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live = tmp_path / "raw" / "live"
    live.mkdir(parents=True)
    base = {"chat": tool.GAME_CHAT, "from": 1, "date": "2026-09-26T01:00:00", "markup": None}
    recs = [
        {**base, "id": 7, "edit_date": None, "text": "a"},
        {**base, "id": 7, "edit_date": "2026-09-26T01:00:05", "text": "b"},
        {**base, "id": 7, "edit_date": "2026-09-26T01:00:05", "text": "c"},
        {**base, "id": 7, "edit_date": "2026-09-26T01:00:05", "text": "c"},
    ]
    (live / "x.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    monkeypatch.setenv("PYROBOT_RESEARCH", str(tmp_path))
    found = tool.find({7}, versions=True)
    assert [(key, rec["text"]) for key, rec in sorted(found.items())] == [
        ((7, "", 0), "a"),
        ((7, "2026-09-26T01:00:05", 0), "b"),
        ((7, "2026-09-26T01:00:05", 1), "c"),
    ]
