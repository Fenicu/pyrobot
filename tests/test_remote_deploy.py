import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "remote-deploy.sh"

# Подставной docker: пишет вызовы в журнал; ps отдаёт сервисы из PS_SERVICES (или падает при
# PS_FAIL), pg_dump пишет тело дампа, падает при DUMP_FAIL или обрывает выкат сигналом при
# DUMP_KILL, остальное успешно.
FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$CALLS"
case "$*" in
  *"ps --status running --services"*)
    if [ -n "${PS_FAIL:-}" ]; then exit 1; fi
    printf '%s' "$PS_SERVICES" ;;
  *pg_dump*)
    if [ -n "${DUMP_FAIL:-}" ]; then echo "partial" ; exit 1; fi
    if [ -n "${DUMP_KILL:-}" ]; then echo "partial"; kill -HUP "$PPID"; sleep 1; exit 1; fi
    echo "dump-body" ;;
esac
exit 0
"""


def _deploy(
    tmp_path: Path,
    *,
    services: str = "postgres\npyrobot\n",
    dump_fail: bool = False,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    deploy_dir = tmp_path / "pyrobot"
    deploy_dir.mkdir(exist_ok=True)
    (deploy_dir / ".env").write_text("X=1\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "calls.log"
    calls.write_text("", encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DEPLOY_DIR": str(deploy_dir),
        "CALLS": str(calls),
        "PS_SERVICES": services,
        "READY_ATTEMPTS": "1",
    }
    if dump_fail:
        env["DUMP_FAIL"] = "1"
    env.update(extra_env or {})
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, encoding="utf-8", check=False
    )
    return result, calls.read_text(encoding="utf-8").splitlines()


def _index(calls: list[str], needle: str) -> int:
    return next(i for i, call in enumerate(calls) if needle in call)


def _backups(tmp_path: Path) -> Path:
    return tmp_path / "pyrobot" / "backups"


def test_dump_written_before_migrate(tmp_path: Path) -> None:
    result, calls = _deploy(tmp_path)
    assert result.returncode == 0, result.stderr
    dumps = list(_backups(tmp_path).glob("pre-deploy-*.dump"))
    assert len(dumps) == 1
    assert dumps[0].read_text(encoding="utf-8") == "dump-body\n"
    assert stat.S_IMODE(dumps[0].stat().st_mode) == 0o600
    assert not list(_backups(tmp_path).glob("*.tmp"))
    dump = next(c for c in calls if "pg_dump" in c)
    assert "exec -T postgres pg_dump -U pyrobot --format=custom pyrobot" in dump
    assert _index(calls, "pg_dump") < _index(calls, "run --rm migrate")


def test_failed_dump_aborts_deploy(tmp_path: Path) -> None:
    result, calls = _deploy(tmp_path, dump_fail=True)
    assert result.returncode == 1
    assert not any("migrate" in c or "up -d" in c for c in calls)
    assert list(_backups(tmp_path).iterdir()) == []


def test_first_deploy_skips_dump(tmp_path: Path) -> None:
    result, calls = _deploy(tmp_path, services="")
    assert result.returncode == 0, result.stderr
    assert not any("pg_dump" in c for c in calls)
    assert any("run --rm migrate" in c for c in calls)
    assert "dump skipped" in result.stdout


def test_keeps_three_newest_pre_deploy_dumps(tmp_path: Path) -> None:
    backups = _backups(tmp_path)
    backups.mkdir(parents=True)
    old = [f"pre-deploy-2026010{i}T000000Z.dump" for i in range(1, 5)]
    daily = "pyrobot-2026-01-01.dump"
    for name in (*old, daily):
        (backups / name).write_text("old", encoding="utf-8")
    result, _ = _deploy(tmp_path)
    assert result.returncode == 0, result.stderr
    left = sorted(p.name for p in backups.glob("pre-deploy-*.dump"))
    assert len(left) == 3
    assert left[:2] == old[-2:]
    assert (backups / daily).exists()


def test_failed_dump_keeps_existing_dumps(tmp_path: Path) -> None:
    backups = _backups(tmp_path)
    backups.mkdir(parents=True)
    keep = backups / "pre-deploy-20260101T000000Z.dump"
    keep.write_text("old", encoding="utf-8")
    result, _ = _deploy(tmp_path, dump_fail=True)
    assert result.returncode == 1
    assert keep.read_text(encoding="utf-8") == "old"
    assert [p.name for p in backups.iterdir()] == [keep.name]


def test_interrupted_dump_leaves_no_tmp(tmp_path: Path) -> None:
    result, calls = _deploy(tmp_path, extra_env={"DUMP_KILL": "1"})
    assert result.returncode != 0
    assert not any("migrate" in c for c in calls)
    assert list(_backups(tmp_path).iterdir()) == []


def test_stale_tmp_removed_before_dump(tmp_path: Path) -> None:
    backups = _backups(tmp_path)
    backups.mkdir(parents=True)
    stale = backups / "pre-deploy-20260101T000000Z.dump.tmp"
    stale.write_text("partial", encoding="utf-8")
    result, _ = _deploy(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not stale.exists()
    assert len(list(backups.glob("pre-deploy-*.dump"))) == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_backups_dir_aborts(tmp_path: Path) -> None:
    backups = _backups(tmp_path)
    backups.mkdir(parents=True)
    backups.chmod(0o500)
    try:
        result, calls = _deploy(tmp_path, extra_env={"USER": "deployer"})
    finally:
        backups.chmod(0o700)
    assert result.returncode == 1
    assert "backups/ is not writable by deployer" in result.stderr
    assert "sudo chown -R deployer: ~/pyrobot/backups" in result.stderr
    assert not any("pg_dump" in c or "migrate" in c for c in calls)


def test_ps_failure_aborts_deploy(tmp_path: Path) -> None:
    result, calls = _deploy(tmp_path, extra_env={"PS_FAIL": "1"})
    assert result.returncode == 1
    assert not any("pg_dump" in c or "migrate" in c or "up -d" in c for c in calls)
