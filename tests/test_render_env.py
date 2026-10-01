import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "render-env.sh"
# Пароли, которые Compose исказил бы без литеральной формы: $ALIAS, ${VAR}, кавычки, #, \.
TRICKY = {
    "POSTGRES_PASSWORD": "db$HOME${PATH}'q'",
    "PYROBOT_ADMIN_PASSWORD": 'it\'s $USER ${X:-y} "dq" #hash a\\b $$',
    "PYROBOT_TG_API_HASH": "abc'def",
    # Ключ шифрования сессий: urlsafe base64 с `-`, `_` и `=`.
    "PYROBOT_SECRET_KEY": "-_---_---_---_---_---_---_---_---_---_--_v8=",
}


def render(env: dict[str, str], *names: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *names],
        env={"PATH": os.environ["PATH"], **env},
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def test_rejects_values_compose_cannot_read() -> None:
    for bad in ("a\\'b", "ends\\", "two\nlines"):
        res = render({"PYROBOT_ADMIN_PASSWORD": bad}, "PYROBOT_ADMIN_PASSWORD")
        assert res.returncode == 1 and res.stdout == ""
        assert "PYROBOT_ADMIN_PASSWORD" in res.stderr and bad not in res.stderr
    assert render({}, "MISSING").returncode == 1


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker compose")
def test_compose_reads_secrets_literally(tmp_path: Path) -> None:
    shutil.copy(ROOT / "compose.yml", tmp_path / "compose.yml")
    res = render(TRICKY, *TRICKY)
    assert res.returncode == 0, res.stderr
    (tmp_path / ".env").write_text(res.stdout, encoding="utf-8")
    out = subprocess.run(
        ["docker", "compose", "-f", "compose.yml", "config", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    # В выводе config литеральный `$` записан как `$$`.
    env = {
        k: v.replace("$$", "$")
        for k, v in json.loads(out.stdout)["services"]["pyrobot"]["environment"].items()
    }
    assert env["PYROBOT_ADMIN_PASSWORD"] == TRICKY["PYROBOT_ADMIN_PASSWORD"]
    assert env["PYROBOT_TG_API_HASH"] == TRICKY["PYROBOT_TG_API_HASH"]
    assert env["PYROBOT_SECRET_KEY"] == TRICKY["PYROBOT_SECRET_KEY"]
    assert env["PYROBOT_DATABASE_URL"] == (
        f"postgresql+asyncpg://pyrobot:{TRICKY['POSTGRES_PASSWORD']}@postgres:5432/pyrobot"
    )
