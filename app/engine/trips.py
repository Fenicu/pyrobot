"""Поездки: какие виды транспорта бот берёт и в каком порядке — чистые функции над настройками."""

from __future__ import annotations

from app.engine.settings import Settings


def trip_vehicles(cfg: Settings) -> tuple[str, ...]:
    """Виды для поездок по приоритету (ключи каталога `VEHICLES`); пусто — поездки выключены."""
    if not cfg.features.trips:
        return ()
    return tuple(cfg.trips.vehicles)
