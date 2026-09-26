import pytest
from pydantic import ValidationError

from app.engine.settings import EngineSection, Settings, SettingsConflict, StaticSettings


def test_defaults_are_safe() -> None:
    s = Settings()
    assert s.engine.mode == "dry_run"
    assert s.engine.killed is False
    assert s.telegram.expected_user_id == 267519921
    assert s.chats.bulls_invite_chat_id is None


async def test_static_update_and_conflict() -> None:
    store = StaticSettings()
    new = await store.update(
        lambda s: s.model_copy(update={"engine": s.engine.model_copy(update={"mode": "live"})}),
        changed_by="test",
    )
    assert new.engine.mode == "live"
    assert store.version == 1
    with pytest.raises(SettingsConflict):
        await store.update(lambda s: s, changed_by="test", expected_version=0)


def test_click_answer_timeout_bounded() -> None:
    assert EngineSection(click_answer_timeout_s=30).click_answer_timeout_s == 30
    with pytest.raises(ValidationError):
        EngineSection(click_answer_timeout_s=30.5)
