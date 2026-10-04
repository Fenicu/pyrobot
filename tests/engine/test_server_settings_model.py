import pytest

from app.engine.server_settings import (
    ServerSettings,
)
from app.engine.settings import SettingsPatchError, patch_model


def test_defaults_match_spec() -> None:
    s = ServerSettings()
    # retention
    assert s.retention.messages_days == 90
    assert s.retention.decisions_days == 30
    assert s.retention.metrics_days == 365
    assert s.retention.ledger_days == 31
    assert s.retention.audit_days == 365
    # invites
    assert s.invites.default_ttl_h == 72
    assert s.invites.default_max_accounts == 1
    # limits
    assert s.limits.max_accounts_total == 50
    assert s.limits.sse_per_user == 5
    assert s.limits.tg_codes_per_hour == 10
    assert s.limits.tg_codes_per_account_hour == 3
    # engine_bounds
    assert s.engine_bounds.min_request_interval_s_min == 1.6
    assert s.engine_bounds.antiflood_pause_s_min == 10.0
    assert s.engine_bounds.antiflood_retry_max_max == 2
    assert s.engine_bounds.action_ttl_s_max == 600.0


def test_patch_model_updates_nested_fields() -> None:
    s = ServerSettings()
    patched = patch_model(
        ServerSettings,
        s.model_dump(mode="json"),
        {"retention": {"messages_days": 180}, "limits": {"sse_per_user": 10}},
    )
    assert patched.retention.messages_days == 180
    assert patched.retention.decisions_days == 30
    assert patched.limits.sse_per_user == 10
    assert patched.limits.max_accounts_total == 50


def test_patch_model_rejects_unknown_path() -> None:
    s = ServerSettings()
    with pytest.raises(SettingsPatchError) as exc_info:
        patch_model(
            ServerSettings,
            s.model_dump(mode="json"),
            {"retention": {"unknown_field": 123}},
        )
    assert exc_info.value.code == "unknown_field"
    assert exc_info.value.path == "retention.unknown_field"
