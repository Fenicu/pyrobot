import json
from pathlib import Path
from typing import Any

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


def _ok_schema(schema: dict[str, Any], path: str, method: str) -> Any:
    return schema["paths"][path][method]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]


def test_engine_tg_and_state_are_typed() -> None:
    schema = build_schema()
    typed = {
        ("/api/v1/engine/status", "get"): "EngineStatusOut",
        ("/api/v1/tg/status", "get"): "TgStatusOut",
        ("/api/v1/tg/login/start", "post"): "TgStatusOut",
        ("/api/v1/tg/login/code", "post"): "TgStatusOut",
        ("/api/v1/tg/login/password", "post"): "TgStatusOut",
        ("/api/v1/tg/logout", "post"): "TgStatusOut",
        ("/api/v1/state", "get"): "StateOut",
    }
    for (path, method), model in typed.items():
        assert _ok_schema(schema, path, method) == {"$ref": f"#/components/schemas/{model}"}
    catalog = schema["components"]["schemas"]["ScenarioInfo"]["properties"]["required"]
    assert catalog["additionalProperties"] == {"$ref": "#/components/schemas/ParamSpec"}
