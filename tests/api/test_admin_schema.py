from typing import Any

import pytest
from fastapi import FastAPI

from app.api.app import create_api
from app.db.base import Database
from tests.api.conftest import make_container

# Имена полей всех ответов /admin/* — только служебные метаданные.
ALLOWED = {
    "id",
    "login",
    "role",
    "created_at",
    "last_login_at",
    "accounts",
    "max_accounts",
    "disabled",
    "disabled_reason",
    "deleting",
    # Служебные поля аккаунтов (задача 9)
    "name",
    "owner_id",
    "owner_login",
    "status",
    "status_reason",
    "blocked",
    "blocked_reason",
    "running",
    "tg_online",
    "restarts_24h",
    "last_error_code",
    "last_error_at",
    "messages_1h",
    "actions_1h",
    "rows",
    # Приглашения, журнал действий и уведомления сервера.
    "expires_at",
    "note",
    "expired",
    "invite",
    "token",
    "path",
    "version",
    "defaults",
    "changed",
    "items",
    "next_before",
    "at",
    "actor_user_id",
    "actor_login",
    "action",
    "target_type",
    "target_id",
    "details",
    "level",
    "code",
    "read",
    # Стандартные поля ошибок FastAPI / Pydantic
    "detail",
    "loc",
    "msg",
    "type",
    "input",
    "ctx",
}

FORBIDDEN = {
    "values",
    "state",
    "journal",
    "text",
    "settings",
    "metro",
    "commands",
    "metrics",
}


@pytest.fixture
def app() -> FastAPI:
    return create_api(
        make_container(Database("postgresql+asyncpg://pyrobot:pyrobot@localhost/unused"))
    )


def _collect_properties(
    schema: dict[str, Any], schemas: dict[str, Any], visited: set[str]
) -> set[str]:
    props: set[str] = set()

    if "$ref" in schema:
        ref_name = schema["$ref"].removeprefix("#/components/schemas/")
        if ref_name in visited:
            return props
        visited.add(ref_name)
        target = schemas.get(ref_name)
        if target:
            props.update(_collect_properties(target, schemas, visited))
        return props

    if "properties" in schema and isinstance(schema["properties"], dict):
        for prop_name, prop_schema in schema["properties"].items():
            props.add(prop_name)
            if isinstance(prop_schema, dict):
                props.update(_collect_properties(prop_schema, schemas, visited))

    if "items" in schema and isinstance(schema["items"], dict):
        props.update(_collect_properties(schema["items"], schemas, visited))

    for key in ("allOf", "anyOf", "oneOf"):
        if key in schema and isinstance(schema[key], list):
            for sub in schema[key]:
                if isinstance(sub, dict):
                    props.update(_collect_properties(sub, schemas, visited))

    if "additionalProperties" in schema and isinstance(schema["additionalProperties"], dict):
        props.update(_collect_properties(schema["additionalProperties"], schemas, visited))

    return props


def test_admin_responses_only_service_fields(app: FastAPI) -> None:
    openapi = app.openapi()
    schemas = openapi.get("components", {}).get("schemas", {})
    admin_paths = {p: ops for p, ops in openapi["paths"].items() if p.startswith("/api/v1/admin")}
    assert admin_paths, "no /api/v1/admin routes found in openapi"
    assert {
        "/api/v1/admin/invites",
        "/api/v1/admin/invites/{invite_id}",
        "/api/v1/admin/server-settings",
        "/api/v1/admin/audit",
        "/api/v1/admin/notifications",
        "/api/v1/admin/notifications/read",
    }.issubset(admin_paths)

    all_props: set[str] = set()
    for path, ops in admin_paths.items():
        path_allowed = ALLOWED.copy()
        path_forbidden = FORBIDDEN.copy()
        if path == "/api/v1/admin/server-settings":
            path_allowed.update({"values", "schema"})
            path_forbidden.difference_update({"values"})
        if path == "/api/v1/admin/notifications":
            path_allowed.add("text")
            path_forbidden.discard("text")
        for op in ops.values():
            if not isinstance(op, dict):
                continue
            responses = op.get("responses", {})
            for resp in responses.values():
                if not isinstance(resp, dict):
                    continue
                content = resp.get("content", {})
                for media in content.values():
                    if isinstance(media, dict) and "schema" in media:
                        props = _collect_properties(media["schema"], schemas, set())
                        all_props.update(props)
                        assert props.issubset(path_allowed), (
                            f"unauthorized fields in {path}: {props - path_allowed}"
                        )
                        assert props.isdisjoint(path_forbidden), (
                            f"forbidden fields in {path}: {props & path_forbidden}"
                        )

    assert all_props, "no response properties collected from /api/v1/admin routes"
    assert "metrics" not in all_props
