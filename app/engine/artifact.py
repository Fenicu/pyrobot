"""Сбор артефакта: тактика (дела по артефакту и уровню персонажа), переходы записи
`artifact_run` вместе с лотереей и вид для API — чистые функции над настройками и состоянием."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.engine.clock import Clock
from app.engine.notify import NotifierPort
from app.engine.parsing.artifacts import COLLECT_SPAN, MAX_LEVEL, RECOLLECTABLE
from app.engine.settings import (
    ArtifactKey,
    ArtifactLottery,
    ArtifactRunSection,
    ArtifactsSection,
    LotterySection,
    Settings,
    SettingsProvider,
)
from app.engine.state.model import ArtifactCollect, CharacterState

log = logging.getLogger(__name__)
ENGINE_BY = "engine"
BOOK_HIGH_LEVEL = 18
IN_PROGRESS = frozenset({"starting", "active", "paused"})
# Лотерея «на максимум»: все билеты до лимита тиража, без запасов — это умолчания секции.
MAX_LOTTERY = LotterySection()
DAY = timedelta(days=1)
# Экран артефактов показывает конец сбора с точностью до часа: концы ближе — один сбор.
ENDS_TOLERANCE = timedelta(hours=2)


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


def same_end(a: datetime, b: datetime) -> bool:
    return abs(a - b) <= ENDS_TOLERANCE


def is_external(run: ArtifactRunSection, collect: ArtifactCollect, now: datetime) -> bool:
    """Сбор в игре — не тот, что ведёт запись: другой артефакт или срок записи прошёл, а конец
    по экрану с ним не сходится."""
    if run.status == "starting":
        return False
    if run.artifact != collect.artifact or run.ends_at is None:
        return True
    return not (run.ends_at > now or same_end(collect.ends_at, run.ends_at))


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


class ArtifactRuns:
    """Сбор артефакта в движке аккаунта: переходы записи через настройки движка и уведомления.
    Переход — функция над текущими настройками внутри `settings.update`: проверка и запись
    атомарны относительно других правок (PATCH, kill, пауза)."""

    def __init__(
        self,
        *,
        settings: SettingsProvider,
        state: Callable[[], CharacterState],
        notifier: NotifierPort,
        clock: Clock,
    ) -> None:
        self._settings = settings
        self._state = state
        self._notifier = notifier
        self._clock = clock
        # Внешний сбор, о котором уже сказали: (артефакт, конец по экрану).
        self._external: tuple[str, datetime] | None = None

    def view(self) -> ArtifactView:
        return artifact_view(self._settings.current, self._state(), self._clock.now())

    async def _apply(self, change: Callable[[Settings], Settings], by: str) -> Settings:
        new, _ = await self._settings.update(change, changed_by=by)
        return new

    async def _quiet(self, change: Callable[[Settings], Settings]) -> Settings | None:
        """Переход движка: запись уже другая (её изменили раньше) — ничего."""
        try:
            return await self._apply(change, ENGINE_BY)
        except ArtifactConflict:
            return None

    async def start(self, artifact: ArtifactKey, *, lottery_max: bool, by: str) -> None:
        now, state = self._clock.now(), self._state()
        await self._apply(
            lambda s: start(s, state, artifact, lottery_max=lottery_max, now=now), by
        )

    async def pause(self, *, by: str) -> None:
        await self._apply(pause, by)

    async def resume(self, *, by: str) -> None:
        await self._apply(resume, by)

    async def cancel(self, *, by: str) -> None:
        run = self._settings.current.artifact_run
        level = artifact_level(self._state(), run.artifact, run.started_at)
        await self._apply(lambda s: cancel(s, level), by)

    async def adopt(self, *, by: str) -> None:
        now = self._clock.now()
        found = game_collect(self._state(), now)
        await self._apply(lambda s: adopt(s, found, now), by)

    async def started(self, status: str, reason: str, details: Mapping[str, Any] | None) -> None:
        """Итог сценария `artifact_start`: «Сбор начат!» — сбор идёт; пересобрать нельзя —
        запуск отменяется. Уже идущий сбор и неясный исход клика решает `tick` по экрану."""
        data = details or {}
        artifact = data.get("artifact")

        def ours(change: Callable[[Settings], Settings]) -> Callable[[Settings], Settings]:
            # Поздний итог запуска другого артефакта (отменён, запрошен новый) — не про эту запись.
            def checked(s: Settings) -> Settings:
                if artifact is not None and s.artifact_run.artifact != artifact:
                    raise ArtifactConflict("other_artifact")
                return change(s)

            return checked

        if status == "done" and "started_at" in data:
            at = datetime.fromisoformat(str(data["started_at"]))
            deed = None if data.get("deed") is None else str(data["deed"])
            new = await self._quiet(ours(lambda s: activate(s, at, deed)))
            if new is not None:
                await self._started(new)
        elif reason == "not_recollectable" and await self._quiet(ours(fail_start)) is not None:
            await self._notifier.notify(
                "warn",
                "artifact_start_failed",
                "artifact cannot be recollected now; start cancelled",
            )

    async def tick(self) -> None:
        """Окончание по сроку и на 100 уровне, сверка запуска по экрану, внешний сбор."""
        now, state = self._clock.now(), self._state()
        run = self._settings.current.artifact_run
        if run.status in ("active", "paused"):
            await self._maybe_finish(run, state, now)
        elif run.status == "starting":
            await self._settle_start(run, state)
        else:
            await self._external_collect(run, state, now)

    async def _maybe_finish(
        self, run: ArtifactRunSection, state: CharacterState, now: datetime
    ) -> None:
        level = artifact_level(state, run.artifact, run.started_at)
        if run.ends_at is not None and now >= run.ends_at:
            code = "artifact_finished"
        elif level is not None and level >= MAX_LEVEL:
            code = "artifact_completed"
        else:
            return
        if await self._quiet(lambda s: finish(s, level)) is None:
            return
        until = run.ends_at.isoformat() if run.ends_at is not None else "?"
        shown = level if level is not None else "?"
        text = f"{run.artifact}: level {shown}; next start after {until}"
        await self._notifier.notify("info", code, text)

    async def _settle_start(self, run: ArtifactRunSection, state: CharacterState) -> None:
        seen = start_seen(run, state)
        if seen is None:
            return
        if seen.artifact == run.artifact:
            new = await self._quiet(lambda s: activate_seen(s, seen))
            if new is not None:
                await self._started(new)
            return
        if await self._quiet(fail_start) is not None:
            await self._notifier.notify(
                "warn",
                "artifact_start_failed",
                f"game already collects {seen.artifact}; start of {run.artifact} cancelled",
            )

    async def _external_collect(
        self, run: ArtifactRunSection, state: CharacterState, now: datetime
    ) -> None:
        found = game_collect(state, now)
        if found is None or not is_external(run, found, now):
            return
        told = self._external
        if told is not None and told[0] == found.artifact and same_end(told[1], found.ends_at):
            return
        self._external = (found.artifact, found.ends_at)
        await self._notifier.notify(
            "info",
            "artifact_collect_external",
            f"game collects {found.artifact} until {found.ends_at.isoformat()}; "
            "use 'adopt' in admin to follow it",
        )

    async def _started(self, settings: Settings) -> None:
        run = settings.artifact_run
        until = run.ends_at.isoformat() if run.ends_at is not None else "?"
        await self._notifier.notify(
            "info", "artifact_started", f"{run.artifact} collect started, ends {until}"
        )
        level = char_level(self._state())
        if tactic_mismatch(settings, level):
            deeds = ", ".join(artifact_deeds(settings.artifacts, run.artifact, level))
            await self._notifier.notify(
                "warn",
                "artifact_tactic_mismatch",
                f"{run.artifact} parts drop on {run.deed_hint}; tactic deeds: {deeds}",
            )
