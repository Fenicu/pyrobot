"""Фикстура «Итогов дня» для админки из тестовых данных:
uv run python tools/daily_fixture.py [файл].

Суммы метрик — ступенчатые ряды по дням, записи журнала прихода — эффекты настоящего редьюсера на
фикстурах игры (книга, карта, добыча с предметами, задание, контейнер, коробка, метро, Горбушка,
ограбление, отель, начало дела), разложенные по дням.
"""

import json
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.api.routes_daily import DailyOut, day_out  # noqa: E402
from app.engine.daily import LedgerEntry, last_by_day, summarize  # noqa: E402
from app.engine.gametime import MSK, tasks_day  # noqa: E402
from app.engine.parsing import default_parser  # noqa: E402
from app.engine.settings import ChatsSection  # noqa: E402
from app.engine.state.ledger import Effect  # noqa: E402
from app.engine.state.reducer import StateReducer  # noqa: E402
from tests.fixtures import game_msg  # noqa: E402

OUT = ROOT / "admin" / "src" / "lib" / "fixtures" / "daily.json"
# 28.09 14:40 MSK: сегодня неполный, журнал прихода — с 22.09 (день запуска — неполный).
NOW = datetime(2026, 9, 28, 14, 40, tzinfo=MSK).astimezone(UTC)
TODAY = tasks_day(NOW)
DAYS = 30
# Итоги — эффекты редьюсера на фикстурах: (семейство, id сообщения).
EVENTS = {
    "book": ("items", 3516680),
    "card": ("items", 3516678),
    "harvest": ("activities", 3517279),
    "harvest_logistic": ("activities", 3610665),
    "harvest_diploma": ("activities", 3624009),
    "job_item": ("activities", 3623749),
    "start": ("activities", 3517276),
    "task": ("daily", 3625831),
    "container": ("items", 3611233),
    "prizebox": ("items", 3517262),
    "metro": ("metro", 3624441),
    "gorbushka": ("gorbushka", 3516739),
    "robbery": ("sleep", 3420239),
    "hotel": ("sleep", 3525189),
    "dividends": ("stocks", 3621194),
}
# Сколько раз итог случился в день (назад от сегодня: 0 — сегодня).
SCHEDULE: dict[int, dict[str, int]] = {
    0: {
        "book": 3,
        "card": 2,
        "harvest": 5,
        "harvest_logistic": 1,
        "job_item": 2,
        "start": 6,
        "task": 1,
        "prizebox": 1,
        "gorbushka": 2,
        "robbery": 1,
    },
    1: {
        "book": 4,
        "card": 3,
        "harvest": 7,
        "harvest_diploma": 1,
        "start": 8,
        "task": 1,
        "container": 1,
        "metro": 1,
        "gorbushka": 4,
        "hotel": 1,
        "dividends": 1,
    },
    2: {"book": 2, "card": 1, "harvest": 4, "start": 4, "gorbushka": 3, "hotel": 1},
    3: {"book": 4, "card": 3, "harvest": 6, "start": 6, "task": 1, "metro": 1, "gorbushka": 4},
    4: {"book": 3, "harvest": 5, "start": 5, "gorbushka": 2},
    5: {"book": 4, "card": 2, "harvest": 6, "start": 6, "task": 1, "gorbushka": 4},
    6: {"book": 1, "harvest": 2, "start": 2},
}
# Значения метрик на конец дня (назад от сегодня); None — точки в эти сутки нет.
SERIES: dict[str, list[int | None]] = {
    "money": [5240, 5000, 2895, 3205, 1335, 480, 1350, 900, 700],
    "exp": [
        17504861,
        17500049,
        17490119,
        17484115,
        17475705,
        17468002,
        17460410,
        17455100,
        17450000,
    ],
    "knowledge": [21995, 21909, 21769, 21711, 21591, 21500, 21420, 21380, 21300],
    "details": [136551, 136671, 136659, 136739, 136759, 136700, 136690, 136640, 136600],
    "raw": [21374, 21310, 21198, None, 21102, 21050, 21000, 20970, 20950],
    "glory": [45270, 45180, 45180, None, 45090, 45000, 44910, 44820, 44820],
    "level": [71, 71, 71, 70, 70, 70, 70, 70, 70],
}


def _effects(family: str, msg_id: int) -> tuple[Effect, ...]:
    msg = game_msg(family, msg_id)
    return StateReducer().reduce({}, msg, default_parser(ChatsSection()).parse(msg))[1]


def _ledger() -> list[LedgerEntry]:
    known = {name: _effects(*ref) for name, ref in EVENTS.items()}
    entries: list[LedgerEntry] = []
    for back, counts in SCHEDULE.items():
        day = TODAY - timedelta(days=back)
        for name, n in counts.items():
            for effect in known[name] * n:
                entries.append(LedgerEntry(day, effect.kind, effect.amounts, effect.items))
    return entries


def _points() -> list[tuple[datetime, str, float]]:
    points: list[tuple[datetime, str, float]] = []
    for key, values in SERIES.items():
        for back, value in enumerate(values):
            if value is None:
                continue
            day = TODAY - timedelta(days=back)
            at = time(14, 30) if back == 0 else time(22, 0)
            moment = datetime.combine(day, at, tzinfo=MSK).astimezone(UTC)
            points.append((moment, key, float(value)))
    return points


def build() -> dict[str, Any]:
    since = TODAY - timedelta(days=max(SCHEDULE))
    summary = summarize(
        today=TODAY,
        days=DAYS,
        last=last_by_day(_points()),
        level_before=None,
        ledger=_ledger(),
        ledger_since=since,
    )
    out = DailyOut(days=[day_out(d) for d in summary], ledger_since=since)
    return out.model_dump(mode="json")


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    text = json.dumps(build(), ensure_ascii=False, indent=1)
    out.write_text(text + "\n", encoding="utf-8")
    print(f"written {out}")


if __name__ == "__main__":
    main()
