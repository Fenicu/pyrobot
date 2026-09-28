import re
from pathlib import Path

from app.engine.daily import DEED, INCOME_ORDER, LOSS_ORDER
from app.engine.parsing.screens import _RESULTS
from app.engine.state import reducer

ROOT = Path(__file__).resolve().parent.parent
TEXT = ROOT / "admin" / "src" / "lib" / "daily" / "text.ts"


def _reducer_kinds() -> set[str]:
    source = Path(reducer.__file__).read_text(encoding="utf-8")
    direct = set(re.findall(r'p\.effect\(\s*"(\w+)"', source))
    results = {reducer._RESULT_KINDS.get(name, name) for name, _ in _RESULTS}
    return direct | results


def test_every_effect_kind_is_grouped_for_daily() -> None:
    """Каждый вид эффекта редьюсера — в разовом, в тратах и потерях или итог дела."""
    assert _reducer_kinds() == {*INCOME_ORDER, *LOSS_ORDER, DEED}


def test_admin_dictionary_names_every_kind() -> None:
    block = re.search(
        r"export const KIND[^=]*= \{(.*?)\n\};", TEXT.read_text(encoding="utf-8"), re.S
    )
    assert block is not None
    keys = set(re.findall(r"^\t(\w+): \{", block.group(1), re.M))
    assert keys == {*INCOME_ORDER, *LOSS_ORDER, DEED}
