from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

# Фазы бюджета (§11): с 60% бюджета без найденного выхода — разведка фронтира, с 85% —
# сигнал «риск опоздания». Путь к выходу закладывается с запасом ×1.5.
FRONTIER_AT = 0.6
LATE_AT = 0.85
ROUTE_SAFETY = 1.5
# Время шага — по недавним ходам «клик → новый кадр» (без диалогов событий и простоя); пока
# замеров мало, оно не меньше априорного (по бафу быстрого шага).
MEASURED_AFTER = 10
RECENT_MOVES = 20
STEP_FAST_S = 5.0
STEP_SLOW_S = 20.0
# Игра выкидывает из метро за 15 минут до битвы с половиной найденного.
GAME_KICK = timedelta(minutes=15)


def prior_step_s(fast_move: bool) -> float:
    return STEP_FAST_S if fast_move else STEP_SLOW_S


@dataclass(frozen=True, slots=True)
class Budget:
    """Время забега: от первого кадра карты до битвы минус запас (15 мин игры + свой)."""

    started: datetime
    battle_at: datetime | None
    margin: timedelta
    step_prior_s: float = STEP_FAST_S

    def total_s(self) -> float | None:
        if self.battle_at is None:
            return None
        return (self.battle_at - self.margin - self.started).total_seconds()

    def kick_at(self) -> datetime | None:
        """Когда игра выкинет из метро сама (экрана выброса не видели)."""
        return self.battle_at - GAME_KICK if self.battle_at is not None else None

    def used(self, now: datetime) -> float:
        total = self.total_s()
        if total is None:
            return 0.0
        if total <= 0:
            return 1.0
        return (now - self.started).total_seconds() / total

    def step_s(self, recent: Sequence[float]) -> float:
        """Время шага для планов: медиана недавних ходов, пока их мало — не меньше априорного."""
        moves = list(recent)[-RECENT_MOVES:]
        if not moves:
            return self.step_prior_s
        measured = median(moves)
        return measured if len(moves) >= MEASURED_AFTER else max(measured, self.step_prior_s)

    def optimistic_step_s(self, recent: Sequence[float]) -> float:
        """Время шага для необратимых решений: меньшее из недавнего замера и априорного."""
        moves = list(recent)[-RECENT_MOVES:]
        return min(median(moves), self.step_prior_s) if moves else self.step_prior_s

    def must_leave(self, now: datetime, route: int, step_s: float) -> bool:
        """Пора к выходу: путь × время шага × 1.5 + запас не меньше, чем осталось до битвы."""
        if self.battle_at is None:
            return False
        need = timedelta(seconds=route * step_s * ROUTE_SAFETY) + self.margin
        return now + need >= self.battle_at
