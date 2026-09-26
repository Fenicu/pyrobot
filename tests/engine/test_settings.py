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


def test_strategy_defaults_follow_spec() -> None:
    s = Settings()
    assert (s.strategy.weight_xp, s.strategy.weight_money) == (1.0, 1.0)
    assert (s.strategy.weight_resources, s.strategy.weight_team) == (0.5, 0.5)
    assert s.features.books and s.features.gorbushka and not s.features.lottery
    assert not s.features.casino and not s.features.pet_feast and not s.features.daily_tasks
    assert s.food.order == ("hotdog", "pizza", "burger") and s.food.banana_reserve == 50
    assert (s.sleep.duration_h, s.sleep.hotel_if_cash_after_reserve_ge) == (7, None)
    assert s.levelup.policy == "balanced" and not s.engine.paused


def test_sleep_duration_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"sleep": {"duration_h": 13}})


def test_phase4_defaults_follow_spec() -> None:
    s = Settings()
    assert (s.battle.target, s.battle.overrides) == ("📯Pied Piper", {})
    stocks = s.stocks
    assert (stocks.cash_floor, stocks.min_dump, stocks.sell_cap_margin, stocks.dump_lead_min) == (
        150,
        200,
        5,
        5,
    )
    assert s.tangerine.interval_h == 20


def test_battle_target_must_be_known() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"battle": {"target": "☣️Black Mesa"}})
    with pytest.raises(ValidationError):
        Settings.model_validate({"tangerine": {"interval_h": 10}})
