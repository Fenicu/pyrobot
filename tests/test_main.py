import pytest

from app.__main__ import uvicorn_options
from app.config import AppConfig


def test_uvicorn_trusts_only_configured_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYROBOT_FORWARDED_ALLOW_IPS", "10.10.40.3")
    cfg = AppConfig(_env_file=None, transport="fake", http_port=8081)
    opts = uvicorn_options(cfg)
    assert opts["proxy_headers"] is True
    assert opts["forwarded_allow_ips"] == "10.10.40.3"
    assert (opts["port"], opts["workers"]) == (8081, 1)


def test_proxy_defaults_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYROBOT_FORWARDED_ALLOW_IPS", raising=False)
    cfg = AppConfig(_env_file=None, transport="fake")
    assert uvicorn_options(cfg)["forwarded_allow_ips"] == "127.0.0.1"
