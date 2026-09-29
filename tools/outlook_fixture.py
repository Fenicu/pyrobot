"""Фикстуры «Плана бота» для админки из снимка состояния с прода: outlook.json (занят работой) и
outlook_stale.json (занятость устарела, цикл спит до книги):
uv run python tools/outlook_fixture.py."""

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
OUT_STALE = OUT.with_name("outlook_stale.json")
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


def _snapshot() -> dict[str, Any]:
    data: dict[str, Any] = _shifted(json.loads(STATE.read_text(encoding="utf-8"))["state"])
    # Немного 🔥 — чтобы в подсказке было основное дело, которое бот возьмёт следующим.
    data["motivation"] = _seen(5)
    data["sleep_deadline"] = _seen(_iso(NOW + timedelta(hours=8)), "derived")
    data["metro_ready_at"] = _seen(_iso(NOW + timedelta(minutes=50)), "derived")
    return data


def _loop(wait: tuple[str, datetime] | None = None) -> LoopView:
    reason, at = wait if wait is not None else (None, None)
    return LoopView(
        paused=False,
        ready=None,
        auto=True,
        current=None,
        manual_queue=0,
        next_wake=at,
        wait_reason=reason,
        wake_at=at,
    )


def build() -> dict[str, Any]:
    data = _snapshot()
    data["busy"] = _seen({"activity": "job", "until": _iso(NOW + timedelta(minutes=20))})
    data["levelup_pending"] = _seen(True)
    view = outlook(load_state(data), Settings(), NOW, done_today=DONE_TODAY)
    return outlook_out(PlanView(NOW, view, _loop())).model_dump(mode="json")


# Через 20 минут после основной фикстуры: занятость (работа до 19:40 MSK) и 🔥 сняты 26 минут
# назад, остальной профиль — ещё раньше, 💵 — чуть позже занятости; быстрые поля устарели. Цикл
# решил ждать книгу и спит, а план на этот момент — обновить профиль, остальное — по последним
# данным.
# Лотерею цикл отложил на полчаса («тиража нет»): во втором проходе есть отказ до его выбора.
STALE_NOW = NOW + timedelta(minutes=20)
STALE_SEEN = STALE_NOW - timedelta(minutes=26)
STALE_JOB_END = STALE_NOW - timedelta(minutes=10)
STALE_HOLDS = {"lottery_buy": STALE_NOW + timedelta(minutes=30)}


def build_stale() -> dict[str, Any]:
    data = _snapshot()
    job = {"activity": "job", "until": _iso(STALE_JOB_END)}
    data["busy"] = {"value": job, "at": _iso(STALE_SEEN), "src": "screen"}
    data["motivation"] = {**data["motivation"], "at": _iso(STALE_SEEN)}
    state = load_state(data)
    view = outlook(state, Settings(), STALE_NOW, cooldowns=STALE_HOLDS, done_today=DONE_TODAY)
    book = next(w.at for w in view.wakeups if w.kind == "book_ready")
    loop = _loop(("book_ready", book))
    return outlook_out(PlanView(STALE_NOW, view, loop)).model_dump(mode="json")


def main() -> None:
    for out, plan in ((OUT, build()), (OUT_STALE, build_stale())):
        out.write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"written {out}")


if __name__ == "__main__":
    main()
