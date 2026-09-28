"""Офлайн-выгрузка JSON Schema настроек для описаний в админке:
uv run python tools/settings_schema.py [файл]."""

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine.settings import Settings  # noqa: E402

OUT = ROOT / "admin" / "src" / "lib" / "settings" / "settings.schema.json"


def build() -> dict[str, Any]:
    """Тот же источник, что `schema` в `GET /settings`."""
    return Settings.model_json_schema()


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    text = json.dumps(build(), ensure_ascii=False, indent=1, sort_keys=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(f"written {out}")


if __name__ == "__main__":
    main()
