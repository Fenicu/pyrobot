import re
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.engine.settings import (
    ChatsSection,
    EngineSection,
    Settings,
    SettingsConflict,
    SettingsPatchError,
    StaticSettings,
    apply_patch,
    restart_required,
    self_chat_fields,
    settings_diff,
    stored_values,
)


def test_defaults_are_safe() -> None:
    s = Settings()
    assert s.engine.mode == "dry_run"
    assert s.engine.killed is False
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
    assert s.strategy.weight_resources == 0.5
    assert s.strategy.focus == ("harvest", "dconv")
    assert s.strategy.deeds == ("harvest", "job", "learn", "dconv", "walk")
    assert s.features.books and s.features.gorbushka and s.features.lottery
    assert not s.features.casino and not s.features.pet_feast and s.features.daily_tasks
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
        Settings.model_validate({"battle": {"target": "Black Mesa"}})
    assert Settings.model_validate({"battle": {"target": "☣️Black Mesa"}}).battle.target
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


def test_stored_values_overlay_defaults_without_validation() -> None:
    # Настройку, которую сборка уже не принимает, видно как есть — поверх умолчаний.
    defaults = Settings().model_dump(mode="json")
    stored = {
        "engine": {"mode": "warp", "paused": True},
        "strategy": {"reserve_ahead_min": {"metro": 30}},
        "battle": {"overrides": {"13": "🤖Hooli"}},
        "metro": "не секция",
        "telegram": {"expected_user_id": 42},
    }
    values = stored_values(stored)
    assert values["engine"] == {**defaults["engine"], "mode": "warp", "paused": True}
    assert values["strategy"]["reserve_ahead_min"] == {"gorbushka": 60, "metro": 30}
    assert values["battle"]["overrides"] == {"13": "🤖Hooli"}
    # Секция не объектом — по умолчанию, незнакомая отбрасывается, как при `model_validate`.
    assert values["metro"] == defaults["metro"] and "telegram" not in values
    assert values["food"] == defaults["food"]
    assert stored["engine"] == {"mode": "warp", "paused": True}


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
    # Чат мандаринов планировщик и шлюз читают на лету.
    assert restart_required(["chats.tangerine_chat_id", "chats.tangerine_reply_to"]) == []
    # Чат команды шлюз и реакция пересылки сверяют при каждой отправке.
    assert restart_required(["chats.team_chat_id"]) == []


def test_telegram_binding_left_settings() -> None:
    # Привязка к пользователю Telegram — `accounts.tg_user_id`; секции в настройках больше нет.
    assert "telegram" not in Settings.model_fields
    assert "telegram" not in Settings().model_dump()
    # Старые записи с секцией читаются: лишние ключи отбрасываются.
    old = Settings.model_validate(
        {"telegram": {"expected_user_id": 42}, "engine": {"mode": "live"}}
    )
    assert old.engine.mode == "live" and "telegram" not in old.model_dump()
    with pytest.raises(SettingsPatchError) as err:
        apply_patch(Settings(), {"telegram": {"expected_user_id": 1}})
    assert err.value.code == "unknown_field"


def test_smoothie_channel_off_by_default() -> None:
    assert Settings().chats.smoothie_channel_id is None
    patched = apply_patch(Settings(), {"chats": {"smoothie_channel_id": -1001356300612}})
    assert patched.chats.smoothie_channel_id == -1001356300612
    # Без канала его прежний id — обычная супергруппа: годится в чат команды.
    team = apply_patch(Settings(), {"chats": {"team_chat_id": -1001356300612}})
    assert team.chats.team_chat_id == -1001356300612


def test_team_chat_off_by_default() -> None:
    assert Settings().chats.team_chat_id is None
    patched = apply_patch(Settings(), {"chats": {"team_chat_id": -1001149209877}})
    assert patched.chats.team_chat_id == -1001149209877
    assert apply_patch(patched, {"chats": {"team_chat_id": None}}).chats.team_chat_id is None


@pytest.mark.parametrize(
    "chat",
    [
        # Чат игры и личные чаты (положительные id), обычная группа без -100.
        227859379,
        267519921,
        -1149209877,
        1001149209877,
        # Чаты, которые бот уже читает или куда шлёт: SWINFO, канал смузи, мандарины.
        -1001109615116,
        -1001356300612,
        -1001377961602,
    ],
)
def test_team_chat_only_other_supergroup(chat: int) -> None:
    channel = apply_patch(Settings(), {"chats": {"smoothie_channel_id": -1001356300612}})
    with pytest.raises(ValidationError) as err:
        apply_patch(channel, {"chats": {"team_chat_id": chat}})
    assert err.value.errors()[0]["loc"] == ("chats", "team_chat_id")


def test_team_chat_may_be_bulls_invite_chat() -> None:
    # Чат команды — та же супергруппа, куда приходят приглашения на бой с биржевиками.
    bulls = apply_patch(Settings(), {"chats": {"bulls_invite_chat_id": -1001234567890}})
    patched = apply_patch(bulls, {"chats": {"team_chat_id": -1001234567890}})
    assert patched.chats.team_chat_id == patched.chats.bulls_invite_chat_id == -1001234567890
    # Порядок не важен: чат приглашений задают вторым.
    team = apply_patch(Settings(), {"chats": {"team_chat_id": -1001234567890}})
    both = apply_patch(team, {"chats": {"bulls_invite_chat_id": -1001234567890}})
    assert both.chats.team_chat_id == both.chats.bulls_invite_chat_id == -1001234567890


# Версия настроек прода до основных дел: командный вес и явный список дел без прогулки.
PROD_V1 = {
    "strategy": {
        "weight_xp": 1.0,
        "weight_money": 1.0,
        "weight_resources": 0.5,
        "weight_team": 0.5,
        "exp_scale": 200.0,
        "money_scale": 30.0,
        "resource_scale": 10.0,
        "deeds": ["harvest", "job", "learn", "dconv"],
    },
    "features": {"daily_tasks": False},
}


def test_old_settings_with_weight_team_still_load() -> None:
    s = Settings.model_validate(PROD_V1)
    assert s.strategy.deeds == ("harvest", "job", "learn", "dconv")
    assert s.strategy.focus == ("harvest", "dconv")
    assert not s.features.daily_tasks
    assert "weight_team" not in s.model_dump()["strategy"]
    patched = apply_patch(s, {"food": {"banana_reserve": 40}})
    diff = settings_diff(s.model_dump(mode="json"), patched.model_dump(mode="json"))
    assert diff == {"food.banana_reserve": [50, 40]}


def test_weight_team_is_gone_from_patch() -> None:
    with pytest.raises(SettingsPatchError) as err:
        apply_patch(Settings(), {"strategy": {"weight_team": 1.0}})
    assert (err.value.code, err.value.path) == ("unknown_field", "strategy.weight_team")


def test_daily_personal_order() -> None:
    assert Settings().daily.personal_order == (
        "convDets",
        "robPro",
        "jobMoney",
        "materials",
        "learnKnows",
        "walkMoney",
        "confKnows",
    )
    with pytest.raises(ValidationError):
        Settings.model_validate({"daily": {"personal_order": ["convDets", "lottery"]}})
    patched = apply_patch(Settings(), {"daily": {"personal_order": ["jobMoney"]}})
    assert patched.daily.personal_order == ("jobMoney",)


def test_reserve_ahead_defaults_patch_and_saved_dumps() -> None:
    ahead = Settings().strategy.reserve_ahead_min
    assert (ahead.gorbushka, ahead.metro) == (60, 60)
    patched = apply_patch(Settings(), {"strategy": {"reserve_ahead_min": {"metro": 0}}})
    assert (
        patched.strategy.reserve_ahead_min.gorbushka,
        patched.strategy.reserve_ahead_min.metro,
    ) == (60, 0)
    diff = settings_diff(Settings().model_dump(mode="json"), patched.model_dump(mode="json"))
    assert diff == {"strategy.reserve_ahead_min.metro": [60, 0]}
    assert restart_required(diff) == []
    with pytest.raises(ValidationError):
        apply_patch(Settings(), {"strategy": {"reserve_ahead_min": {"gorbushka": -1}}})
    # Сутки — предел: большее число переполнило бы расчёт запаса и остановило цикл планировщика.
    day = apply_patch(Settings(), {"strategy": {"reserve_ahead_min": {"gorbushka": 1440}}})
    assert day.strategy.reserve_ahead_min.gorbushka == 1440
    for leaf in ("gorbushka", "metro"):
        with pytest.raises(ValidationError):
            apply_patch(Settings(), {"strategy": {"reserve_ahead_min": {leaf: 1441}}})
        with pytest.raises(ValidationError):
            apply_patch(Settings(), {"strategy": {"reserve_ahead_min": {leaf: 10**13}}})
    saved = Settings().model_dump(mode="json")
    del saved["strategy"]["reserve_ahead_min"]
    assert Settings.model_validate(saved).strategy.reserve_ahead_min.gorbushka == 60


def test_robbery_defense_on_by_default_and_for_saved_dumps() -> None:
    saved = Settings().model_dump(mode="json")
    del saved["features"]["robbery_defense"]
    assert Settings.model_validate(saved).features.robbery_defense


# Длительности, сроки и таймауты: верхний предел по смыслу поля. Без него огромное значение из
# PATCH переполняло timedelta (OverflowError в планировщике — цикл вставал, в чистке журнала, на
# старте) или надолго останавливало шлюз.
LIMITS: dict[str, int] = {
    "engine.min_request_interval_s": 60,
    "engine.antiflood_pause_s": 600,
    "engine.action_ttl_s": 3600,
    "engine.default_expect_timeout_s": 300,
    "engine.click_answer_timeout_s": 30,
    "engine.recovered_react_max_age_min": 1440,
    "engine.refresh_min_interval_s": 3600,
    "engine.state_stale_after_min": 1440,
    "strategy.reserve_ahead_min.gorbushka": 1440,
    "strategy.reserve_ahead_min.metro": 1440,
    "sleep.duration_h": 12,
    "sleep.lead_min": 1440,
    "stocks.dump_lead_min": 1440,
    "tangerine.interval_h": 168,
    "metro.min_budget_min": 1440,
    "metro.battle_margin_min": 1440,
    "metro.extra_margin_min": 1440,
}
DURATION = re.compile(r"_(s|min|h|days)$")


def nested(path: str, value: object) -> dict[str, Any]:
    head, _, rest = path.partition(".")
    return {head: nested(rest, value) if rest else value}


def limited_settings() -> Settings:
    """Все длительности — на верхнем пределе."""
    data: dict[str, Any] = {}
    for path, limit in LIMITS.items():
        _merge_into(data, nested(path, limit))
    return apply_patch(Settings(), data)


def _merge_into(data: dict[str, Any], extra: dict[str, Any]) -> None:
    for key, value in extra.items():
        if isinstance(value, dict):
            _merge_into(data.setdefault(key, {}), value)
        else:
            data[key] = value


@pytest.mark.parametrize(("path", "limit"), LIMITS.items())
def test_durations_have_upper_limit(path: str, limit: int) -> None:
    value: Any = apply_patch(Settings(), nested(path, limit)).model_dump()
    for key in path.split("."):
        value = value[key]
    assert value == limit
    default: Any = Settings().model_dump()
    for key in path.split("."):
        default = default[key]
    assert default <= limit
    for huge in (limit + 1, 10**13, "inf"):
        with pytest.raises(ValidationError):
            apply_patch(Settings(), nested(path, huge))


def test_every_duration_setting_has_a_limit() -> None:
    """Новая настройка-длительность без предела не пройдёт: путь по имени (`_s`, `_min`, `_h`,
    `_days`, группа `reserve_ahead_min`) — в LIMITS, предел в схеме — тот же."""
    found: dict[str, float | None] = {}

    def walk(model: type[BaseModel], prefix: str) -> None:
        for name, field in model.model_fields.items():
            path = f"{prefix}{name}"
            if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel):
                walk(field.annotation, f"{path}.")
            elif DURATION.search(name) or prefix.endswith("reserve_ahead_min."):
                le = [m.le for m in field.metadata if getattr(m, "le", None) is not None]
                found[path] = le[0] if le else None

    walk(Settings, "")
    assert found == LIMITS


def test_limited_settings_load() -> None:
    assert limited_settings().metro.min_budget_min == 1440


def test_self_chat_fields_only_chat_ids() -> None:
    user = 267519921
    chats = ChatsSection(
        game_chat_id=user, swinfo_user_id=user, tangerine_chat_id=user, tangerine_reply_to=user
    )
    # id сообщения, на которое отвечает /gt, — не чат: совпадение с пользователем не в счёт.
    assert self_chat_fields(Settings(chats=chats), user) == [
        "chats.game_chat_id",
        "chats.swinfo_user_id",
        "chats.tangerine_chat_id",
    ]
    assert self_chat_fields(Settings(chats=ChatsSection(tangerine_reply_to=user)), user) == []


def test_artifact_run_is_read_only_section() -> None:
    with pytest.raises(SettingsPatchError) as err:
        apply_patch(Settings(), {"artifact_run": {"status": "active"}})
    assert (err.value.code, err.value.path) == ("read_only", "artifact_run")
    schema = Settings.model_json_schema()
    run = schema["$defs"]["ArtifactRunSection"]["properties"]
    assert schema["properties"]["artifact_run"]["readOnly"] is True
    assert all(field["readOnly"] for field in run.values())


def test_artifact_tactic_patch_applies() -> None:
    patched = apply_patch(Settings(), {"artifacts": {"book_low": ["job", "walk"]}})
    assert patched.artifacts.book_low == ("job", "walk")
    # Старые настройки без новых секций читаются с умолчаниями.
    old = Settings.model_validate({"engine": {"mode": "live"}})
    assert old.artifact_run.status == "idle" and old.artifacts.light == ("walk",)
