import re
from pathlib import Path

from app.engine.state.reducer import METRIC_FIELDS

SERIES = Path(__file__).resolve().parent.parent / "admin" / "src" / "lib" / "metrics" / "series.ts"
# Пишутся, но без графика: уровень нужен «Итогам дня», а его график почти всегда прямая.
CHARTLESS = {"level"}


def test_every_metric_has_a_chart() -> None:
    """Каждое поле метрик движка, кроме `CHARTLESS`, — на странице «Метрики»; лишних нет."""
    keys = re.findall(r"\{ key: '(\w+)'", SERIES.read_text(encoding="utf-8"))
    assert sorted(keys) == sorted(set(METRIC_FIELDS) - CHARTLESS)
    assert "glory" in keys
    assert CHARTLESS <= set(METRIC_FIELDS)
