import re
from pathlib import Path

from app.engine.state.reducer import METRIC_FIELDS

SERIES = Path(__file__).resolve().parent.parent / "admin" / "src" / "lib" / "metrics" / "series.ts"


def test_every_metric_has_a_chart() -> None:
    """Каждое поле метрик движка — на странице «Метрики»; лишних в админке нет."""
    keys = re.findall(r"\{ key: '(\w+)'", SERIES.read_text(encoding="utf-8"))
    assert sorted(keys) == sorted(METRIC_FIELDS)
    assert "glory" in keys
