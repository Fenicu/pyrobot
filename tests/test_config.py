from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import AppConfig


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("PYROBOT_TRANSPORT", "PYROBOT_COOKIE_SECURE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PYROBOT_ACCOUNT_ID", "5")
    cfg = AppConfig(_env_file=None, tg_api_id=1, tg_api_hash="x")
    assert cfg.transport == "kurigram"
    assert cfg.cookie_secure is True
    # Аккаунт больше не задаётся окружением: хост поднимает все включённые.
    assert not hasattr(cfg, "account_id")


def test_env_override_and_secret_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYROBOT_TG_API_ID", "123")
    monkeypatch.setenv("PYROBOT_TG_API_HASH", "abcdef")
    monkeypatch.setenv("PYROBOT_TRANSPORT", "fake")
    cfg = AppConfig(_env_file=None)
    assert cfg.tg_api_id == 123
    assert cfg.tg_api_hash.get_secret_value() == "abcdef"
    assert cfg.transport == "fake"
    assert "abcdef" not in repr(cfg)


def test_kurigram_requires_api_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("PYROBOT_TRANSPORT", "PYROBOT_TG_API_ID", "PYROBOT_TG_API_HASH"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError, match="tg_api_id"):
        AppConfig(_env_file=None, tg_api_hash="x")
    with pytest.raises(ValidationError, match="tg_api_hash"):
        AppConfig(_env_file=None, tg_api_id=1)
    assert AppConfig(_env_file=None, transport="fake").tg_api_id == 0


def test_admin_dir_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYROBOT_ADMIN_DIR", raising=False)
    assert AppConfig(_env_file=None, transport="fake").admin_dir == Path("/app/admin")
    monkeypatch.setenv("PYROBOT_ADMIN_DIR", "/srv/admin")
    assert AppConfig(_env_file=None, transport="fake").admin_dir == Path("/srv/admin")


def test_secret_key_and_host_limits_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "PYROBOT_SECRET_KEY",
        "PYROBOT_SECRET_KEY_RESET",
        "PYROBOT_DB_POOL_SIZE",
        "PYROBOT_DB_MAX_OVERFLOW",
        "PYROBOT_MAX_ENGINES",
        "PYROBOT_ENGINE_START_GAP_S",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = AppConfig(_env_file=None, transport="fake")
    assert cfg.secret_key is None and cfg.secret_key_reset is False
    assert (cfg.db_pool_size, cfg.db_max_overflow, cfg.max_engines) == (4, 4, 20)
    assert cfg.engine_start_gap_s == 3.0


def test_secret_key_from_env_is_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYROBOT_SECRET_KEY", "s3cr3t-key-value")
    monkeypatch.setenv("PYROBOT_SECRET_KEY_RESET", "1")
    monkeypatch.setenv("PYROBOT_MAX_ENGINES", "5")
    monkeypatch.setenv("PYROBOT_ENGINE_START_GAP_S", "0.5")
    cfg = AppConfig(_env_file=None, transport="fake")
    assert cfg.secret_key is not None and cfg.secret_key.get_secret_value() == "s3cr3t-key-value"
    assert cfg.secret_key_reset is True and cfg.max_engines == 5
    assert cfg.engine_start_gap_s == 0.5
    assert "s3cr3t-key-value" not in repr(cfg)
