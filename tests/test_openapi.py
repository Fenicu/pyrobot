import json
from pathlib import Path
from typing import Any, get_args

from app.api.openapi import build_schema
from app.engine.planner.types import WakeKind

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
        ("/api/v1/planner/outlook", "get"): "OutlookOut",
    }
    for (path, method), model in typed.items():
        assert _ok_schema(schema, path, method) == {"$ref": f"#/components/schemas/{model}"}
    catalog = schema["components"]["schemas"]["ScenarioInfo"]["properties"]["required"]
    assert catalog["additionalProperties"] == {"$ref": "#/components/schemas/ParamSpec"}


def _schemas() -> dict[str, Any]:
    return build_schema()["components"]["schemas"]  # type: ignore[no-any-return]


def test_state_schema_is_public_model() -> None:
    schemas = _schemas()
    state = schemas["StateOut"]["properties"]["state"]
    assert state == {"$ref": "#/components/schemas/PublicState"}
    public = schemas["PublicState"]
    assert "applied" not in public["properties"]
    # Поля не обязательны: до первого сообщения снимок пуст.
    assert "required" not in public
    assert public["properties"]["money"]["anyOf"] == [
        {"$ref": "#/components/schemas/Observed_int_"},
        {"type": "null"},
    ]
    assert schemas["Observed_int_"]["required"] == ["value", "at", "src"]
    assert public["properties"]["prices"]["additionalProperties"] == {
        "$ref": "#/components/schemas/Observed_PriceState_"
    }


def _error_ref(schema: dict[str, Any], path: str, method: str, code: str) -> Any:
    response = schema["paths"][path][method]["responses"][code]
    return response["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]


def test_error_responses_in_detail_envelope() -> None:
    schema = build_schema()
    schemas = schema["components"]["schemas"]
    for path in ("/api/v1/commands/send", "/api/v1/commands/click"):
        assert _error_ref(schema, path, "post", "409") == "ConfirmRequiredOut"
        assert _error_ref(schema, path, "post", "403") == "ErrorOut"
        assert _error_ref(schema, path, "post", "503") == "ErrorOut"
    assert schemas["ConfirmRequiredOut"]["properties"]["detail"] == {
        "$ref": "#/components/schemas/ConfirmRequired"
    }
    assert _error_ref(schema, "/api/v1/settings", "patch", "409") == "VersionConflictOut"
    conflict = schemas["VersionConflict"]
    assert conflict["required"] == ["code", "version"]
    assert conflict["properties"]["code"]["const"] == "version_conflict"
    assert schemas["ErrorOut"]["required"] == ["detail"]
    assert _error_ref(schema, "/api/v1/state", "get", "401") == "ErrorOut"
    assert _error_ref(schema, "/api/v1/state", "get", "503") == "ErrorOut"
    assert _error_ref(schema, "/api/v1/settings", "patch", "403") == "ErrorOut"


def test_session_routes_document_401() -> None:
    paths = build_schema()["paths"]
    open_paths = {"/api/v1/auth/login", "/healthz", "/readyz"}
    for path, ops in paths.items():
        if path in open_paths:
            continue
        for method, op in ops.items():
            assert "401" in op["responses"], (method, path)


def test_plan_timer_kinds_are_closed_enum() -> None:
    kind = _schemas()["PlanTimerOut"]["properties"]["kind"]
    assert kind["enum"] == list(get_args(WakeKind))
