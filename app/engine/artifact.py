"""Сбор артефакта: тактика (дела по артефакту и уровню персонажа), переходы записи
`artifact_run` вместе с лотереей и вид для API — чистые функции над настройками и состоянием."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.engine.parsing.artifacts import COLLECT_SPAN, MAX_LEVEL, RECOLLECTABLE
from app.engine.settings import (
    ArtifactKey,
    ArtifactLottery,
    ArtifactRunSection,
    ArtifactsSection,
    LotterySection,
    Settings,
)
from app.engine.state.model import ArtifactCollect, CharacterState

BOOK_HIGH_LEVEL = 18
IN_PROGRESS = frozenset({"starting", "active", "paused"})
# Лотерея «на максимум»: все билеты до лимита тиража, без запасов — это умолчания секции.
MAX_LOTTERY = LotterySection()
DAY = timedelta(days=1)


class ArtifactConflict(Exception):
    """Переход сбора сейчас невозможен; `code` — код ответа API: `run_in_progress`,
    `locked_until`, `artifact_max`, `deeds_disabled`, `no_run`, `not_collecting`,
    `not_external`, `other_artifact`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def artifact_deeds(
    tactic: ArtifactsSection, artifact: str | None, level: int | None
) -> tuple[str, ...]:
    """Дела, где падают части `artifact`, по приоритету; уровень персонажа неизвестен — как с
    18-го."""
    if artifact == "fax":
        return tuple(tactic.fax)
    if artifact == "light":
        return tuple(tactic.light)
    if artifact == "book":
        low = level is not None and level < BOOK_HIGH_LEVEL
        return tuple(tactic.book_low if low else tactic.book_high)
    return ()


def collecting(run: ArtifactRunSection, now: datetime) -> bool:
    """Режим сбора: вся 🔥 — в дела артефакта."""
    return run.status == "active" and run.ends_at is not None and now < run.ends_at


def char_level(state: CharacterState) -> int | None:
    return state.level.value if state.level is not None else None


def artifact_level(
    state: CharacterState, artifact: str | None, since: datetime | None = None
) -> int | None:
    """Уровень артефакта по экрану или строке части, наблюдённый не раньше `since`."""
    seen = state.artifacts
    if seen is None or artifact is None or (since is not None and seen.at < since):
        return None
    return seen.value.get(artifact)


def game_collect(state: CharacterState, now: datetime) -> ArtifactCollect | None:
    """Идущий в игре сбор по последнему экрану артефактов; кончившийся — None."""
    seen = state.artifact_collect
    collect = seen.value if seen is not None else None
    return collect if collect is not None and collect.ends_at > now else None


def is_external(run: ArtifactRunSection, collect: ArtifactCollect, now: datetime) -> bool:
    """Сбор в игре — не тот, что ведёт запись: другой артефакт или срок записи прошёл."""
    if run.status == "starting":
        return False
    ours = run.artifact == collect.artifact and run.ends_at is not None and run.ends_at > now
    return not ours


def start_seen(run: ArtifactRunSection, state: CharacterState) -> ArtifactCollect | None:
    """Экран артефактов после нажатия «Запустить» показал идущий сбор."""
    seen = state.artifact_collect
    if run.status != "starting" or run.requested_at is None or seen is None:
        return None
    return seen.value if seen.at >= run.requested_at else None


def tactic_mismatch(settings: Settings, level: int | None) -> bool:
    """Игра сказала, что части падают не в делах тактики."""
    run = settings.artifact_run
    deeds = artifact_deeds(settings.artifacts, run.artifact, level)
    return run.deed_hint is not None and run.deed_hint not in deeds


def lottery_of(settings: Settings) -> ArtifactLottery:
    return ArtifactLottery(enabled=settings.features.lottery, lottery=settings.lottery)


def _with_run(settings: Settings, run: ArtifactRunSection) -> Settings:
    return settings.model_copy(update={"artifact_run": run})


def _set_lottery(settings: Settings, value: ArtifactLottery) -> Settings:
    features = settings.features.model_copy(update={"lottery": value.enabled})
    return settings.model_copy(update={"features": features, "lottery": value.lottery})


def _require(run: ArtifactRunSection, *statuses: str) -> None:
    if run.status not in statuses:
        raise ArtifactConflict("no_run")


def restore_lottery(settings: Settings) -> Settings:
    """Вернуть лотерею, включённую ботом на сбор, если пользователь её с тех пор не менял."""
    run = settings.artifact_run
    if run.lottery_applied is None or run.lottery_before is None:
        return settings
    if lottery_of(settings) == run.lottery_applied:
        settings = _set_lottery(settings, run.lottery_before)
    return _with_run(settings, run.model_copy(update={"lottery_applied": None}))


def start_blocker(
    settings: Settings, state: CharacterState, artifact: str, now: datetime
) -> str | None:
    run = settings.artifact_run
    if run.status in IN_PROGRESS:
        return "run_in_progress"
    if run.ends_at is not None and run.ends_at > now:
        return "locked_until"
    if game_collect(state, now) is not None:
        return "locked_until"
    if artifact_level(state, artifact) == MAX_LEVEL:
        return "artifact_max"
    if not settings.features.deeds:
        return "deeds_disabled"
    return None


def start(
    settings: Settings,
    state: CharacterState,
    artifact: ArtifactKey,
    *,
    lottery_max: bool,
    now: datetime,
) -> Settings:
    """Запрос на запуск: снимок лотереи, при `lottery_max` и выключенной лотерее — она на
    максимум; сам сбор в игре запустит шаг планировщика `artifact_start`."""
    if (code := start_blocker(settings, state, artifact, now)) is not None:
        raise ArtifactConflict(code)
    before = lottery_of(settings)
    applied: ArtifactLottery | None = None
    if lottery_max and not settings.features.lottery:
        applied = ArtifactLottery(enabled=True, lottery=MAX_LOTTERY)
        settings = _set_lottery(settings, applied)
    run = ArtifactRunSection(
        artifact=artifact,
        status="starting",
        requested_at=now,
        lottery_before=before,
        lottery_applied=applied,
    )
    return _with_run(settings, run)


def pause(settings: Settings) -> Settings:
    run = settings.artifact_run
    _require(run, "active")
    return _with_run(settings, run.model_copy(update={"status": "paused"}))


def resume(settings: Settings) -> Settings:
    run = settings.artifact_run
    _require(run, "paused")
    return _with_run(settings, run.model_copy(update={"status": "active"}))


def cancel(settings: Settings, level: int | None) -> Settings:
    """Отмена: из `starting` в игре ничего не начато — записи нет; из сбора — `cancelled`,
    таймер в игре дотикает, новый сбор — после `ends_at`."""
    run = settings.artifact_run
    _require(run, *IN_PROGRESS)
    settings = restore_lottery(settings)
    if run.status == "starting":
        return _with_run(settings, ArtifactRunSection())
    update = {"status": "cancelled", "result_level": level}
    return _with_run(settings, settings.artifact_run.model_copy(update=update))


def finish(settings: Settings, level: int | None) -> Settings:
    run = settings.artifact_run
    _require(run, "active", "paused")
    settings = restore_lottery(settings)
    update = {"status": "finished", "result_level": level}
    return _with_run(settings, settings.artifact_run.model_copy(update=update))


def fail_start(settings: Settings) -> Settings:
    _require(settings.artifact_run, "starting")
    return _with_run(restore_lottery(settings), ArtifactRunSection())


def activate(settings: Settings, started_at: datetime, deed: str | None) -> Settings:
    """«Сбор начат!»: сбор идёт 10 суток с момента сообщения."""
    run = settings.artifact_run
    _require(run, "starting")
    update = {
        "status": "active",
        "started_at": started_at,
        "ends_at": started_at + COLLECT_SPAN,
        "deed_hint": deed,
    }
    return _with_run(settings, run.model_copy(update=update))


def activate_seen(settings: Settings, collect: ArtifactCollect) -> Settings:
    """Исход клика неясен, но экран артефактов показал наш сбор: конец — по таймеру экрана."""
    run = settings.artifact_run
    _require(run, "starting")
    if collect.artifact != run.artifact:
        raise ArtifactConflict("other_artifact")
    update = {
        "status": "active",
        "started_at": collect.ends_at - COLLECT_SPAN,
        "ends_at": collect.ends_at,
    }
    return _with_run(settings, run.model_copy(update=update))


def adopt(settings: Settings, collect: ArtifactCollect | None, now: datetime) -> Settings:
    """«Вести сбор»: идущий в игре сбор без записи; лотерея не трогается."""
    run = settings.artifact_run
    if run.status in IN_PROGRESS:
        raise ArtifactConflict("run_in_progress")
    if collect is None:
        raise ArtifactConflict("not_collecting")
    if not is_external(run, collect, now):
        raise ArtifactConflict("not_external")
    adopted = ArtifactRunSection.model_validate(
        {
            "artifact": collect.artifact,
            "status": "active",
            "started_at": collect.ends_at - COLLECT_SPAN,
            "ends_at": collect.ends_at,
        }
    )
    return _with_run(settings, adopted)


@dataclass(frozen=True, slots=True)
class Pace:
    levels_per_day: float | None
    forecast_level: int | None


@dataclass(frozen=True, slots=True)
class ArtifactView:
    """Сбор для админки: запись, уровень собираемого (итоговый у отменённого и завершённого),
    уровни всех артефактов, идущий в игре сбор (`external` — не наш), дела по артефактам,
    лотерея, темп и прогноз, когда можно следующий сбор."""

    run: ArtifactRunSection
    level: int | None
    levels: dict[str, int]
    collecting: ArtifactCollect | None
    external: bool
    tactic: dict[str, tuple[str, ...]]
    lottery_on: bool
    lottery_on_start: bool
    pace: Pace
    next_start_at: datetime | None


def pace(run: ArtifactRunSection, state: CharacterState, now: datetime) -> Pace:
    """Уровней в сутки с начала сбора и уровень к его концу при том же темпе; меньше часа сбора
    или уровень неизвестен — без темпа."""
    level = artifact_level(state, run.artifact, run.started_at)
    started, ends = run.started_at, run.ends_at
    if run.status not in ("active", "paused") or started is None or ends is None or level is None:
        return Pace(None, None)
    days = (now - started) / DAY
    if days < 1 / 24:
        return Pace(None, None)
    rate = level / days
    left = max((ends - now) / DAY, 0.0)
    return Pace(round(rate, 1), min(MAX_LEVEL, int(level + rate * left)))


def artifact_view(settings: Settings, state: CharacterState, now: datetime) -> ArtifactView:
    run = settings.artifact_run
    collect = game_collect(state, now)
    final = run.status in ("cancelled", "finished")
    level = run.result_level if final else artifact_level(state, run.artifact, run.started_at)
    known = (run.ends_at, collect.ends_at if collect is not None else None)
    ends = [t for t in known if t is not None and t > now]
    seen = state.artifacts
    return ArtifactView(
        run=run,
        level=level,
        levels=dict(seen.value) if seen is not None else {},
        collecting=collect,
        external=collect is not None and is_external(run, collect, now),
        tactic={
            a: artifact_deeds(settings.artifacts, a, char_level(state)) for a in RECOLLECTABLE
        },
        lottery_on=settings.features.lottery,
        lottery_on_start=settings.artifacts.lottery_on_start,
        pace=pace(run, state, now),
        next_start_at=max(ends) if ends else None,
    )
