"""Фикстура «Плана бота» для админки из снимка состояния с прода:
uv run python tools/outlook_fixture.py [файл]."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.api.routes_planner import outlook_out  # noqa: E402
from app.engine.planner.decide import outlook  # noqa: E402
from app.engine.planner.loop import LoopView, PlanView  # noqa: E402
from app.engine.settings import Settings  # noqa: E402
from app.engine.state.model import load_state  # noqa: E402

STATE = ROOT / "tests" / "fixtures" / "api" / "state.json"
OUT = ROOT / "admin" / "src" / "lib" / "fixtures" / "outlook.json"
# Снимок с прода снят во сне (во сне план — только таймеры). Копия сдвинута на 2 ч 40 мин назад, в
# 19:30 MSK (идёт продажа лотереи), персонаж занят работой, ждёт прокачки, метро откроется через
# 50 минут и ляжет спать к ночи — в плане есть отказы до решения, «готово сейчас», запас 🔥 под
# метро и таймеры.
SHIFT = timedelta(hours=-2, minutes=-40)
NOW = datetime(2026, 9, 27, 16, 30, tzinfo=UTC)
SEEN = NOW - timedelta(minutes=1)
DONE_TODAY = {"deed:harvest": 3, "deed:dconv": 2}


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _shifted(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _shifted(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shifted(v) for v in value]
    if isinstance(value, str) and "T" in value:
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return value
        return _iso(moment + SHIFT)
    return value


def _seen(value: Any, src: str = "screen") -> dict[str, Any]:
    return {"value": value, "at": _iso(SEEN), "src": src}


def build() -> dict[str, Any]:
    data = _shifted(json.loads(STATE.read_text(encoding="utf-8"))["state"])
    data["busy"] = _seen({"activity": "job", "until": _iso(NOW + timedelta(minutes=20))})
    data["levelup_pending"] = _seen(True)
    # Немного 🔥 — чтобы в подсказке было основное дело, которое бот возьмёт следующим.
    data["motivation"] = _seen(5)
    data["sleep_deadline"] = _seen(_iso(NOW + timedelta(hours=8)), "derived")
    data["metro_ready_at"] = _seen(_iso(NOW + timedelta(minutes=50)), "derived")
    view = outlook(load_state(data), Settings(), NOW, done_today=DONE_TODAY)
    loop = LoopView(
        paused=False, ready=None, auto=True, current=None, manual_queue=0, next_wake=None
    )
    return outlook_out(PlanView(NOW, view, loop)).model_dump(mode="json")


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    text = json.dumps(build(), ensure_ascii=False, indent=1)
    out.write_text(text + "\n", encoding="utf-8")
    print(f"written {out}")


if __name__ == "__main__":
    main()
