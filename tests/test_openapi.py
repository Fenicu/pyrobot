import json
from pathlib import Path

from app.api.openapi import build_schema

ROOT = Path(__file__).resolve().parent.parent


def test_committed_openapi_is_current() -> None:
    committed = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    assert committed == build_schema(), "openapi.json устарел: uv run python tools/openapi.py"


def test_schema_covers_admin_contract() -> None:
    paths = build_schema()["paths"]
    for path in (
        "/api/v1/settings",
        "/api/v1/journal",
        "/api/v1/commands/send",
        "/api/v1/scenarios/{name}/run",
        "/api/v1/metrics",
        "/api/v1/events",
        "/readyz",
    ):
        assert path in paths
