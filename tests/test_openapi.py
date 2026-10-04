import json
from pathlib import Path
from typing import Any, get_args

from app.api.openapi import build_schema
from app.engine.planner.types import WakeKind

ROOT = Path(__file__).resolve().parent.parent
A = "/api/v1/accounts/{account_id}"


def test_committed_openapi_is_current() -> None:
    committed = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    assert committed == build_schema(), "openapi.json устарел: uv run python tools/openapi.py"


def test_schema_covers_admin_contract() -> None:
    paths = build_schema()["paths"]
    for path in (
        f"{A}/settings",
        f"{A}/journal",
        f"{A}/commands/send",
        f"{A}/scenarios/{{name}}/run",
        f"{A}/metrics",
        f"{A}/daily",
        f"{A}/events",
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
        (f"{A}/engine/status", "get"): "EngineStatusOut",
        (f"{A}/tg/status", "get"): "TgStatusOut",
        (f"{A}/tg/login/start", "post"): "TgStatusOut",
        (f"{A}/tg/login/code", "post"): "TgStatusOut",
        (f"{A}/tg/login/password", "post"): "TgStatusOut",
        (f"{A}/tg/logout", "post"): "TgStatusOut",
        (f"{A}/state", "get"): "StateOut",
        (f"{A}/planner/outlook", "get"): "OutlookOut",
        (f"{A}/daily", "get"): "DailyOut",
        (f"{A}/artifact", "get"): "ArtifactOut",
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


def test_state_schema_has_gadgets() -> None:
    schemas = _schemas()
    public = schemas["PublicState"]
    assert public["properties"]["gadgets"]["anyOf"] == [
        {"$ref": "#/components/schemas/Observed_GadgetsState_"},
        {"type": "null"},
    ]
    gadgets = schemas["GadgetsState"]["properties"]
    assert gadgets["items"]["items"] == {"$ref": "#/components/schemas/GadgetState"}
    assert gadgets["sets"]["items"] == {"type": "string"}
    assert set(schemas["GadgetState"]["properties"]) == {
        "grade",
        "level",
        "slot",
        "name",
        "bonuses",
        "mark",
    }


def _error_ref(schema: dict[str, Any], path: str, method: str, code: str) -> Any:
    response = schema["paths"][path][method]["responses"][code]
    return response["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]


def test_error_responses_in_detail_envelope() -> None:
    schema = build_schema()
    schemas = schema["components"]["schemas"]
    for path in (f"{A}/commands/send", f"{A}/commands/click"):
        assert _error_ref(schema, path, "post", "409") == "ConfirmRequiredOut"
        assert _error_ref(schema, path, "post", "403") == "ErrorOut"
        assert _error_ref(schema, path, "post", "503") == "ErrorOut"
    assert schemas["ConfirmRequiredOut"]["properties"]["detail"] == {
        "$ref": "#/components/schemas/ConfirmRequired"
    }
    # 409 правки настроек: конфликт версии или `account_deleting`.
    conflict_409 = schema["paths"][f"{A}/settings"]["patch"]["responses"]["409"]
    assert conflict_409["content"]["application/json"]["schema"]["anyOf"] == [
        {"$ref": "#/components/schemas/VersionConflictOut"},
        {"$ref": "#/components/schemas/ErrorOut"},
    ]
    conflict = schemas["VersionConflict"]
    assert conflict["required"] == ["code", "version"]
    assert conflict["properties"]["code"]["const"] == "version_conflict"
    assert schemas["ErrorOut"]["required"] == ["detail"]
    assert _error_ref(schema, f"{A}/state", "get", "401") == "ErrorOut"
    assert _error_ref(schema, f"{A}/state", "get", "404") == "ErrorOut"
    assert _error_ref(schema, f"{A}/engine/kill", "post", "503") == "ErrorOut"
    assert _error_ref(schema, f"{A}/settings", "patch", "403") == "ErrorOut"


def test_session_routes_document_401() -> None:
    paths = build_schema()["paths"]
    open_paths = {"/api/v1/auth/login", "/healthz", "/readyz"}
    for path, ops in paths.items():
        if path in open_paths:
            continue
        for method, op in ops.items():
            assert "401" in op["responses"], (method, path)


def test_account_routes_document_404() -> None:
    # Чужой и несуществующий аккаунт — 404 на каждом пути аккаунта; каталог сценариев общий.
    paths = build_schema()["paths"]
    account = {p: ops for p, ops in paths.items() if p.startswith(A)}
    assert "/api/v1/scenarios" in paths and account
    for path, ops in account.items():
        for method, op in ops.items():
            assert "account not found" in op["responses"]["404"]["description"], (method, path)
            path_params = {p["name"] for p in op["parameters"] if p["in"] == "path"}
            assert "account_id" in path_params, (method, path)


def test_plan_timer_kinds_are_closed_enum() -> None:
    kind = _schemas()["PlanTimerOut"]["properties"]["kind"]
    assert kind["enum"] == list(get_args(WakeKind))


def test_daily_level_uses_from_to() -> None:
    level = _schemas()["LevelOut"]
    assert level["required"] == ["from", "to"]
    params = build_schema()["paths"][f"{A}/daily"]["get"]["parameters"]
    days = next(p["schema"] for p in params if p["name"] == "days")
    assert (days["minimum"], days["maximum"], days["default"]) == (1, 30, 30)
