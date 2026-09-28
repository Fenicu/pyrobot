import json

from tools import settings_schema as tool

HINT = "settings.schema.json устарел: uv run python tools/settings_schema.py"


def test_committed_settings_schema_is_current() -> None:
    assert json.loads(tool.OUT.read_text(encoding="utf-8")) == tool.build(), HINT
