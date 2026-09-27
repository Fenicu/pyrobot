"""Офлайн-выгрузка OpenAPI для TS-типов админки: uv run python tools/openapi.py [файл]."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.api.openapi import build_schema  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "openapi.json"
    text = json.dumps(build_schema(), ensure_ascii=False, indent=2, sort_keys=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(f"written {out}")


if __name__ == "__main__":
    main()
