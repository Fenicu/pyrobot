import os
import re
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


CHANGES_STEP = "- name: Запись в CHANGES.rst для тега"


def _changes_step() -> tuple[str, str]:
    """Условие и скрипт шага проверки CHANGES.rst из job lint."""
    lines = (ROOT / ".forgejo" / "workflows" / "ci.yml").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == CHANGES_STEP)
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) <= indent
        ),
        len(lines),
    )
    step = lines[start + 1 : end]
    cond = next(line.strip() for line in step if line.strip().startswith("if:"))
    run = next(i for i, line in enumerate(step) if line.strip() == "run: |")
    body = step[run + 1 :]
    pad = min(len(line) - len(line.lstrip()) for line in body if line.strip())
    return cond, "\n".join(line[pad:] for line in body)


def _check_changes(
    tmp_path: Path, tag: str, changes: str | None
) -> subprocess.CompletedProcess[str]:
    if changes is not None:
        (tmp_path / "CHANGES.rst").write_text(changes, encoding="utf-8")
    _, script = _changes_step()
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "TAG": tag},
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def test_tag_deploy_requires_changes_entry(tmp_path: Path) -> None:
    # Выкатка по тегу без записи в CHANGES.rst дала бы окно «Что нового» без этой версии; шаг
    # стоит в lint — от него зависят image и deploy, а на пушах без тега он не идёт.
    cond, _ = _changes_step()
    assert cond == "if: startsWith(github.ref, 'refs/tags/v')"
    real = (ROOT / "CHANGES.rst").read_text(encoding="utf-8")
    top = re.search(r"^(\d+\.\d+\.\d+) — ", real, re.M)
    assert top is not None
    assert _check_changes(tmp_path, f"v{top[1]}", real).returncode == 0
    entry = "0.1.10 — 03.10.2026\n-------------------\n"
    assert _check_changes(tmp_path, "v0.1.10", entry).returncode == 0
    for tag, changes in [
        ("v0.1.1", entry),  # 0.1.10 — не 0.1.1
        ("v0.1.10", "0.1.10\n------\n"),  # без даты
        ("v0.1.10", "- Готовим 0.1.10 — 03.10.2026\n"),  # не заголовок
        ("v0.1.10", None),  # файла нет
    ]:
        if changes is None:
            (tmp_path / "CHANGES.rst").unlink(missing_ok=True)
        failed = _check_changes(tmp_path, tag, changes)
        assert failed.returncode != 0, (tag, changes)
        assert f"CHANGES.rst has no entry for {tag}" in failed.stderr
