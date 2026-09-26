"""Извлечение реальных сообщений игры из ~/pyrobot-research в тестовые фикстуры."""

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

GAME_CHAT = 227859379
FIELDS = ("id", "chat", "date", "edit_date", "text", "markup")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "game"


def research_dir() -> Path:
    return Path(os.environ.get("PYROBOT_RESEARCH", Path.home() / "pyrobot-research"))


def _records(base: Path) -> Iterator[dict[str, Any]]:
    for path in sorted((base / "raw").glob("*/*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                if "id" in rec and "text" in rec:
                    yield rec
    screens = base / "crawl" / "screens.jsonl"
    if screens.exists():
        with screens.open(encoding="utf-8") as fh:
            for line in fh:
                for rec in json.loads(line).get("resp", []):
                    if rec.get("id"):
                        yield rec


def find(ids: set[int]) -> dict[int, dict[str, Any]]:
    found: dict[int, dict[str, Any]] = {}
    for rec in _records(research_dir()):
        if rec.get("chat") == GAME_CHAT and rec["id"] in ids and not rec.get("out"):
            found.setdefault(rec["id"], {k: rec.get(k) for k in FIELDS})
    return found


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: tools/fixtures.py <family> <id> [<id> ...]", file=sys.stderr)
        return 2
    family, ids = argv[0], {int(a) for a in argv[1:]}
    target = OUT / f"{family}.jsonl"
    existing: dict[int, dict[str, Any]] = {}
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            existing[rec["id"]] = rec
    found = find(ids - existing.keys())
    missing = ids - existing.keys() - found.keys()
    if missing:
        print(f"not found: {sorted(missing)}", file=sys.stderr)
        return 1
    existing.update(found)
    OUT.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(existing[k], ensure_ascii=False) for k in sorted(existing)]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{target.name}: {len(existing)} messages")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
