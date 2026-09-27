import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker compose")


def _config(tmp_path: Path) -> dict[str, Any]:
    shutil.copy(ROOT / "compose.yml", tmp_path / "compose.yml")
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD='x'\n", encoding="utf-8")
    out = subprocess.run(
        ["docker", "compose", "-f", "compose.yml", "config", "--format", "json"],
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
