import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker compose")


def _config(tmp_path: Path, env: str | None = "POSTGRES_PASSWORD='x'\n") -> dict[str, Any]:
    shutil.copy(ROOT / "compose.yml", tmp_path / "compose.yml")
    if env is not None:
        (tmp_path / ".env").write_text(env, encoding="utf-8")
    out = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "migrate",
            "-f",
            "compose.yml",
            "config",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)  # type: ignore[no-any-return]


def test_bot_memory_is_limited(tmp_path: Path) -> None:
    bot = _config(tmp_path)["services"]["pyrobot"]
    assert int(bot["mem_limit"]) == 1024**3


def test_backups_readable_only_by_owner(tmp_path: Path) -> None:
    script = "\n".join(_config(tmp_path)["services"]["backup"]["command"])
    assert "umask 077" in script
    assert script.index("umask 077") < script.index("pg_dump")


def _ports(bot: dict[str, Any]) -> list[tuple[str, str, int]]:
    return [(p["host_ip"], p["published"], p["target"]) for p in bot["ports"]]


def _author_deploy_env(tmp_path: Path) -> None:
    """.env, который пишет выкатка автора (шаг deploy в CI), с подставными секретами."""
    lines = (ROOT / ".forgejo" / "workflows" / "ci.yml").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if "export PYROBOT_IMAGE=" in line)
    end = next(i for i in range(start, len(lines)) if '> "$ENV_FILE"' in lines[i])
    secrets = ("POSTGRES_PASSWORD", "PYROBOT_TG_API_ID", "PYROBOT_TG_API_HASH")
    subprocess.run(
        ["bash", "-c", "\n".join(line.strip() for line in lines[start : end + 1])],
        cwd=ROOT,
        env={
            "PATH": os.environ["PATH"],
            "IMAGE": "git.fenicu.com/fenicu/pyrobot",
            "TAG": "v9.9.9",
            "ENV_FILE": str(tmp_path / ".env"),
            "PYROBOT_ADMIN_PASSWORD": "p",
            **dict.fromkeys(secrets, "1"),
        },
        check=True,
    )


def test_defaults_for_any_server(tmp_path: Path) -> None:
    services = _config(tmp_path)["services"]
    bot = services["pyrobot"]
    assert _ports(bot) == [("127.0.0.1", "8080", 8080)]
    assert bot["image"] == "git.fenicu.com/fenicu/pyrobot:latest"
    trusted = bot["environment"]["PYROBOT_FORWARDED_ALLOW_IPS"]
    assert trusted == "127.0.0.1,172.16.0.0/12,192.168.0.0/16"
    # Сборка из исходников — только у бота: migrate берёт тот же образ, второй сборки нет.
    assert bot["build"]["context"] == str(tmp_path)
    assert "build" not in services["migrate"]
    assert services["migrate"]["image"] == bot["image"]


def test_author_deploy_keeps_apps_address(tmp_path: Path) -> None:
    # На apps заняты 8080–8088 и 8090 (qBittorrent), к боту ходит Caddy с web (10.10.40.3).
    _author_deploy_env(tmp_path)
    bot = _config(tmp_path, env=None)["services"]["pyrobot"]
    assert _ports(bot) == [("10.10.40.20", "8089", 8080)]
    assert bot["image"] == "git.fenicu.com/fenicu/pyrobot:v9.9.9"
    assert bot["environment"]["PYROBOT_FORWARDED_ALLOW_IPS"] == "10.10.40.3"
