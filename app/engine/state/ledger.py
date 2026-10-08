"""Эффекты редьюсера для журнала прихода: что применённый итог сообщения изменил в ресурсах."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

from app.engine.parsing.common import Rewards

# Ключи сумм эффекта: ресурсы, улучшения, 🏆 за задания и контейнеры. 🔥 и 🔋 не пишутся.
AMOUNT_KEYS = (
    "money",
    "exp",
    "knowledge",
    "details",
    "raw",
    "startup_progress",
    "keys",
    "upgrades_white",
    "upgrades_blue",
    "upgrades_red",
    "trophies",
    "containers_small",
    "containers_medium",
)


@dataclass(frozen=True, slots=True)
class Effect:
    """Эффект одного события: вид (`deed`, `book`, `hotel`, …), суммы по `AMOUNT_KEYS` (со
    знаком, без нулей) и предметы крафта. `at` — момент исхода, если это не момент ревизии
    сообщения (отчёты битвы и фабрики датируются самой битвой). `key` — постоянный ключ итога,
    который приходит разными сообщениями (отчёты фабрики и битвы на каждый запрос): в журнале
    прихода с этим ключом один ряд на весь срок его хранения."""

    kind: str
    amounts: dict[str, int]
    items: dict[str, int] = field(default_factory=dict)
    at: datetime | None = None
    key: str | None = None


def numbered(effects: Iterable[Effect]) -> Iterator[tuple[Effect, int]]:
    """Эффекты сообщения с номером среди эффектов того же вида (часть ключа журнала прихода)."""
    seen: dict[str, int] = {}
    for effect in effects:
        seq = seen.get(effect.kind, 0)
        seen[effect.kind] = seq + 1
        yield effect, seq


def amounts(rewards: Rewards | None = None, /, **extra: int) -> dict[str, int]:
    """Ненулевые суммы итога и дополнительных полей."""
    out: dict[str, int] = {}
    if rewards is not None:
        for key in AMOUNT_KEYS:
            value = getattr(rewards, key, 0)
            if value:
                out[key] = value
    for key, value in extra.items():
        if value:
            out[key] = out.get(key, 0) + value
    return out
