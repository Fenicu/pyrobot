import re
from pathlib import Path

from app.api.state_schema import PublicState

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "admin" / "src" / "lib" / "plan" / "store.svelte.ts"
PLANNER = ROOT / "app" / "engine" / "planner"


def _ignored() -> list[str]:
    block = re.search(r"IGNORED = new Set\(\[(.*?)\]\)", STORE.read_text(encoding="utf-8"), re.S)
    assert block is not None
    return re.findall(r"'(\w+)'", block.group(1))


def test_plan_ignores_only_fields_the_planner_does_not_read() -> None:
    """Админка не перечитывает «План бота» на изменение этих полей состояния: планировщик не
    должен их читать (ни `self.s.<поле>`, ни имя поля строкой)."""
    ignored = _ignored()
    assert ignored
    assert set(ignored) <= set(PublicState.model_fields)
    source = "".join(p.read_text(encoding="utf-8") for p in sorted(PLANNER.glob("*.py")))
    read = [f for f in ignored if re.search(rf"(\.s\.{f}\b|[\"']{f}[\"'])", source)]
    assert read == [], f"планировщик читает поля из IGNORED в {STORE.name}"
