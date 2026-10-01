import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = 'if [ -z "${PYROBOT_SECRET_KEY:-}" ]; then'


def _run(script: str, key: str | None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"]}
    if key is not None:
        env["PYROBOT_SECRET_KEY"] = key
    return subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, encoding="utf-8", check=False
    )


def test_deploy_refuses_empty_secret_key() -> None:
    # `.env` выкатки пересоздаётся целиком: пустой секрет — отказ до scp и миграций, а не бот в
    # цикле падений после них.
    lines = (ROOT / ".forgejo" / "workflows" / "ci.yml").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == GUARD)
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "fi")
    remote = next(i for i, line in enumerate(lines) if line.strip().startswith(("ssh ", "scp ")))
    assert end < remote
    guard = "\n".join(line.strip() for line in lines[start : end + 1])
    missing, empty = _run(guard, None), _run(guard, "")
    assert missing.returncode == empty.returncode == 1
    assert "PYROBOT_SECRET_KEY" in empty.stderr
    assert _run(guard, "-_---_---_---_---_---_---_---_---_---_--_v8=").returncode == 0
