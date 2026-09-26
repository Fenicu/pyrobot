"""Извлечение реальных сообщений игры из ~/pyrobot-research в тестовые фикстуры."""

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

GAME_CHAT = 227859379
FIELDS = ("id", "chat", "from", "date", "edit_date", "text", "markup")
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


Key = tuple[int, str]


def _key(rec: dict[str, Any]) -> Key:
    return rec["id"], rec.get("edit_date") or ""


def find(
    ids: set[int], chat: int = GAME_CHAT, *, include_out: bool = False, versions: bool = False
) -> dict[Key, dict[str, Any]]:
    """Первая встреченная запись каждого сообщения; с `versions` — каждой его правки."""
    found: dict[Key, dict[str, Any]] = {}
    seen: set[int] = set()
    for rec in _records(research_dir()):
        if rec.get("out") and not include_out:
            continue
        if rec.get("chat") != chat or rec["id"] not in ids:
            continue
        if not versions and rec["id"] in seen:
            continue
        seen.add(rec["id"])
        found.setdefault(_key(rec), {k: rec.get(k) for k in FIELDS})
    return found


def main(argv: list[str]) -> int:
    chat, include_out, versions = GAME_CHAT, False, False
    while argv[:1] in (["--chat"], ["--include-out"], ["--versions"]):
        if argv[0] == "--include-out":
            include_out, argv = True, argv[1:]
        elif argv[0] == "--versions":
            versions, argv = True, argv[1:]
        else:
            chat, argv = int(argv[1]), argv[2:]
    if len(argv) < 2:
        print(
            "usage: tools/fixtures.py [--chat <id>] [--include-out] [--versions] "
            "<family> <id> [<id> ...]",
            file=sys.stderr,
        )
        return 2
    family, ids = argv[0], {int(a) for a in argv[1:]}
    target = OUT / f"{family}.jsonl"
    existing: dict[Key, dict[str, Any]] = {}
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            existing[_key(rec)] = rec
    known = {msg_id for msg_id, _ in existing}
    # С --versions доснимаем и новые правки уже сохранённых сообщений.
    found = find(
        ids if versions else ids - known, chat, include_out=include_out, versions=versions
    )
    missing = ids - known - {msg_id for msg_id, _ in found}
    if missing:
        print(f"not found: {sorted(missing)}", file=sys.stderr)
        return 1
    for key, rec in found.items():
        existing.setdefault(key, rec)
    OUT.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(existing[k], ensure_ascii=False) for k in sorted(existing)]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{target.name}: {len(existing)} messages")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
