import pytest
from pydantic import ValidationError

from app.engine.settings import (
    EngineSection,
    Settings,
    SettingsConflict,
    SettingsPatchError,
    StaticSettings,
    apply_patch,
    restart_required,
    settings_diff,
)


def test_defaults_are_safe() -> None:
    s = Settings()
    assert s.engine.mode == "dry_run"
    assert s.engine.killed is False
    assert s.telegram.expected_user_id == 267519921
    assert s.chats.bulls_invite_chat_id is None


async def test_static_update_and_conflict() -> None:
    store = StaticSettings()
    new, version = await store.update(
        lambda s: s.model_copy(update={"engine": s.engine.model_copy(update={"mode": "live"})}),
        changed_by="test",
    )
    assert new.engine.mode == "live"
    assert version == store.version == 1
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
    assert Settings().features.battle
    with pytest.raises(ValidationError):
        Settings.model_validate({"tangerine": {"interval_h": 10}})


@pytest.mark.parametrize("hour", [-1, 24])
def test_battle_override_hour_bounds(hour: int) -> None:
    ok = Settings.model_validate({"battle": {"overrides": {0: "🛡Защита", 23: "🤖Hooli"}}})
    assert set(ok.battle.overrides) == {0, 23}
    with pytest.raises(ValidationError):
        Settings.model_validate({"battle": {"overrides": {hour: "🛡Защита"}}})


def test_metro_defaults_follow_spec() -> None:
    m = Settings().metro
    assert (m.min_budget_min, m.battle_margin_min, m.extra_margin_min) == (60, 15, 10)
    assert m.buffs == ("fastMove", "strong", "firstAid")
    assert (m.heal_at, m.heal_before_exit, m.chest_min_packs) == (50, True, 2)
    assert (m.npc_low_enabled, m.npc_high_enabled, m.npc_min_stamina) == (True, False, 30)
    assert Settings().features.metro is True


def test_metro_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"metro": {"battle_margin_min": 10}})
    with pytest.raises(ValidationError):
        Settings.model_validate({"metro": {"buffs": ["fastMove", "coins"]}})
    with pytest.raises(ValidationError):
        Settings.model_validate({"metro": {"heal_at": 120}})


def test_patch_merges_sections_and_replaces_leaves() -> None:
    s = apply_patch(
        Settings(),
        {"engine": {"min_request_interval_s": 2.0}, "food": {"order": ["banana"]}},
    )
    assert s.engine.min_request_interval_s == 2.0 and s.engine.action_ttl_s == 60.0
    assert s.food.order == ("banana",) and s.food.banana_reserve == 50


def test_patch_replaces_mapping_fields_whole() -> None:
    base = apply_patch(Settings(), {"battle": {"overrides": {"13": "🤖Hooli"}}})
    s = apply_patch(base, {"battle": {"overrides": {"22": "🛡Защита"}}})
    assert s.battle.overrides == {22: "🛡Защита"}


@pytest.mark.parametrize(
    ("changes", "code", "path"),
    [
        ({"engine": {"moed": "live"}}, "unknown_field", "engine.moed"),
        ({"nope": {}}, "unknown_field", "nope"),
        ({"engine": {"killed": False}}, "read_only", "engine.killed"),
        ({"engine": {"kill_reason": None}}, "read_only", "engine.kill_reason"),
        ({"engine": {"paused": True}}, "read_only", "engine.paused"),
        ({"engine": 1}, "section_expected", "engine"),
    ],
)
def test_patch_rejects_bad_paths(changes: dict[str, object], code: str, path: str) -> None:
    with pytest.raises(SettingsPatchError) as err:
        apply_patch(Settings(), changes)
    assert (err.value.code, err.value.path) == (code, path)


def test_patch_validates_values() -> None:
    with pytest.raises(ValidationError):
        apply_patch(Settings(), {"sleep": {"duration_h": 13}})


def test_read_only_marked_in_schema() -> None:
    engine = Settings.model_json_schema()["$defs"]["EngineSection"]["properties"]
    assert engine["killed"]["readOnly"] and engine["paused"]["readOnly"]
    assert "readOnly" not in engine["mode"]


def test_settings_diff_lists_leaf_paths() -> None:
    old = Settings().model_dump(mode="json")
    new = apply_patch(
        Settings(), {"engine": {"mode": "live"}, "battle": {"overrides": {"13": "🤖Hooli"}}}
    ).model_dump(mode="json")
    assert settings_diff(old, new) == {
        "engine.mode": ["dry_run", "live"],
        "battle.overrides.13": [None, "🤖Hooli"],
    }
    assert settings_diff(new, new) == {}


def test_restart_required_paths() -> None:
    assert restart_required(["chats.game_chat_id", "engine.mode"]) == ["chats.game_chat_id"]
    assert restart_required(["engine.recovered_react_max_age_min"]) == [
        "engine.recovered_react_max_age_min"
    ]
    assert restart_required(["telegram.expected_user_id"]) == ["telegram.expected_user_id"]
