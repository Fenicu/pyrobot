import pytest

from app.config import AppConfig


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("PYROBOT_TRANSPORT", "PYROBOT_COOKIE_SECURE", "PYROBOT_ACCOUNT_ID"):
        monkeypatch.delenv(key, raising=False)
    cfg = AppConfig(_env_file=None)
    assert cfg.transport == "kurigram"
    assert cfg.cookie_secure is True
    assert cfg.account_id == 1


def test_env_override_and_secret_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYROBOT_TG_API_ID", "123")
    monkeypatch.setenv("PYROBOT_TG_API_HASH", "abcdef")
    monkeypatch.setenv("PYROBOT_TRANSPORT", "fake")
    cfg = AppConfig(_env_file=None)
    assert cfg.tg_api_id == 123
    assert cfg.tg_api_hash.get_secret_value() == "abcdef"
    assert cfg.transport == "fake"
    assert "abcdef" not in repr(cfg)
