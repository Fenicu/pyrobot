from pathlib import Path

import pytest

from app.__main__ import trusted_proxies, uvicorn_options
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


def test_open_requests_do_not_hold_shutdown() -> None:
    # Бесконечный SSE иначе держит SIGTERM до SIGKILL, и Runtime.stop не выполняется.
    cfg = AppConfig(_env_file=None, transport="fake")
    assert uvicorn_options(cfg)["timeout_graceful_shutdown"] == 5


ROUTE = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "eth0\t00000000\t010014AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    "eth0\t000014AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
)


def test_gateway_is_container_default_route(tmp_path: Path) -> None:
    # Прокси на этом же сервере приходит в контейнер с адреса шлюза сети проекта (docker-proxy):
    # его и только его — из маршрута по умолчанию контейнера (010014AC → 172.20.0.1).
    route = tmp_path / "route"
    route.write_text(ROUTE)
    assert trusted_proxies("gateway", route) == "172.20.0.1"
    assert trusted_proxies("10.10.40.3, gateway", route) == "10.10.40.3,172.20.0.1"
    assert trusted_proxies("10.10.40.3", route) == "10.10.40.3"


def test_gateway_unknown_trusts_nobody(tmp_path: Path) -> None:
    # Маршрута по умолчанию нет (сеть none, запуск вне Docker) — X-Forwarded-For не верим никому.
    route = tmp_path / "route"
    route.write_text(ROUTE.splitlines(keepends=True)[0] + ROUTE.splitlines(keepends=True)[2])
    assert trusted_proxies("gateway", route) == "127.0.0.1"
    assert trusted_proxies("gateway", tmp_path / "missing") == "127.0.0.1"
    assert trusted_proxies("192.168.1.10,gateway", tmp_path / "missing") == "192.168.1.10"


def test_uvicorn_resolves_gateway(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    route = tmp_path / "route"
    route.write_text(ROUTE)
    monkeypatch.setattr("app.__main__.ROUTE_TABLE", route)
    monkeypatch.setenv("PYROBOT_FORWARDED_ALLOW_IPS", "gateway")
    cfg = AppConfig(_env_file=None, transport="fake")
    assert uvicorn_options(cfg)["forwarded_allow_ips"] == "172.20.0.1"
