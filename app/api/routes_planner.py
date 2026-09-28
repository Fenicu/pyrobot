from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import SessionContext, current_session
from app.api.errors import AUTH, ENGINE_NOT_STARTED, error
from app.api.routes_engine import facade
from app.engine.facade import EngineFacade, PlannerUnavailable
from app.engine.planner.decide import Phase
from app.engine.planner.loop import PlanView
from app.engine.planner.types import Act, WakeKind, Wakeup
from app.engine.state.model import BusyState

router = APIRouter(prefix="/api/v1", tags=["planner"])
PLANNER_NOT_STARTED = "planner not started"


class PlanCandidateOut(BaseModel):
    scenario: str
    params: dict[str, Any]
    score: float | None
    # `chosen`, `ok` или причина отказа (`busy`, `no_motivation`, `reserved` — 🔥 хватило бы без
    # запасов, `stale:<поле>`, …).
    verdict: str


class PlanActOut(BaseModel):
    scenario: str
    params: dict[str, Any]
    reason: str


class PlanDecisionOut(BaseModel):
    kind: Literal["act", "wait"]
    scenario: str | None
    params: dict[str, Any]
    # Как в журнале решений: у ожидания — `kind` или `kind:key` его таймера либо `no_timers`.
    reason: str
    until: datetime | None


class PlanTimerOut(BaseModel):
    at: datetime
    kind: WakeKind
    # Ключ кулдауна (`cooldown`) или источник обновления (`refresh`).
    key: str | None
    # Во сне: таймер прохода «как после пробуждения».
    after_wake: bool


class PlanLoopOut(BaseModel):
    paused: bool
    # Причина, по которой цикл не исполняет решения (`paused`, `killed`, `spending_blocked`,
    # `pipeline_unhealthy`, `lock_lost`, `tg_offline`), или null.
    ready: str | None
    # Планировщик принимает свои решения (иначе цикл исполняет только ручные запуски).
    auto: bool
    current: str | None
    manual_queue: int
    next_wake: datetime | None


class PlanFocusOut(BaseModel):
    deed: str
    today: int


class PlanNextDeedOut(BaseModel):
    # `deed:<дело>`.
    deed: str
    # personal/team — под личное или командное задание, focus — основное по очереди, best —
    # лучшее по оценке (основные сейчас недоступны).
    why: Literal["personal", "team", "focus", "best"]


class PlanReserveOut(BaseModel):
    # gorbushka — под бой Горбушки, metro — под вход в метро.
    kind: Literal["gorbushka", "metro"]
    motivation: int
    # Момент боя или открытия метро; уже доступное — момент плана.
    at: datetime


class PlanHintsOut(BaseModel):
    # Цель ближайшей битвы по настройкам (своя на её час или общая); null — время следующей битвы
    # неизвестно или устарело.
    battle_target: str | None
    # Билеты за тираж по настройкам: число или `max` по валютам.
    lottery_tickets: dict[str, int | Literal["max"]]
    sleep_hours: int
    # Место сна по правилу сценария на текущих деньгах; null — деньги неизвестны или цена отеля
    # не видена.
    sleep_place: Literal["hotel", "bridge"] | None
    # Дело, которое шаг дел взял бы следующим среди доступных сейчас, и почему; null — ни одно не
    # доступно (нет 🔥, 💵, ⚙️, окно битвы) или нужные поля устарели.
    next_deed: PlanNextDeedOut | None


class OutlookOut(BaseModel):
    now: datetime
    # unknown — занятость неизвестна или устарела; asleep — сон; busy — занят делом; free.
    phase: Phase
    busy: BusyState | None
    decision: PlanDecisionOut
    # Кандидаты до решения («почему не другое»).
    considered: list[PlanCandidateOut]
    # Тоже готово на текущем снимке (не очередь).
    also_ready: list[PlanActOut]
    wakeups: list[PlanTimerOut]
    loop: PlanLoopOut
    focus: list[PlanFocusOut]
    hints: PlanHintsOut
    # 🔥, которые дела сейчас не тратят (`strategy.reserve_ahead_min`), по времени; пусто — нет.
    reserves: list[PlanReserveOut]


def _timer(w: Wakeup, after_wake: bool) -> PlanTimerOut:
    # Окна по часам планировщик считает в МСК: наружу — единообразно в UTC.
    return PlanTimerOut(at=w.at.astimezone(UTC), kind=w.kind, key=w.key, after_wake=after_wake)


def outlook_out(view: PlanView) -> OutlookOut:
    o = view.outlook
    d = o.decision
    if isinstance(d, Act):
        decision = PlanDecisionOut(
            kind="act", scenario=d.scenario, params=d.params, reason=d.reason, until=None
        )
    else:
        decision = PlanDecisionOut(
            kind="wait",
            scenario=None,
            params={},
            reason=d.reason,
            until=d.until.astimezone(UTC) if d.until is not None else None,
        )
    timers = [_timer(w, False) for w in o.wakeups] + [_timer(w, True) for w in o.after_wake]
    loop = view.loop
    return OutlookOut(
        now=view.now,
        phase=o.phase,
        busy=o.busy,
        decision=decision,
        considered=[
            PlanCandidateOut(
                scenario=c.scenario, params=c.params, score=c.score, verdict=c.verdict
            )
            for c in o.considered
        ],
        also_ready=[
            PlanActOut(scenario=a.scenario, params=a.params, reason=a.reason) for a in o.also_ready
        ],
        wakeups=sorted(timers, key=lambda t: t.at),
        loop=PlanLoopOut(
            paused=loop.paused,
            ready=loop.ready,
            auto=loop.auto,
            current=loop.current,
            manual_queue=loop.manual_queue,
            next_wake=loop.next_wake,
        ),
        focus=[PlanFocusOut(deed=deed, today=today) for deed, today in o.focus],
        hints=PlanHintsOut(
            battle_target=o.hints.battle_target,
            lottery_tickets=o.hints.lottery_tickets,
            sleep_hours=o.hints.sleep_hours,
            sleep_place=o.hints.sleep_place,
            next_deed=(
                PlanNextDeedOut(deed=o.hints.next_deed.deed, why=o.hints.next_deed.why)
                if o.hints.next_deed is not None
                else None
            ),
        ),
        reserves=[
            PlanReserveOut(kind=r.kind, motivation=r.motivation, at=r.at.astimezone(UTC))
            for r in o.reserves
        ],
    )


@router.get(
    "/planner/outlook",
    response_model=OutlookOut,
    responses={**AUTH, 503: error(ENGINE_NOT_STARTED, PLANNER_NOT_STARTED)},
)
async def planner_outlook(
    _: Annotated[SessionContext, Depends(current_session)],
    f: Annotated[EngineFacade, Depends(facade)],
) -> OutlookOut:
    """«План бота»: что планировщик решил бы сейчас, почему не другое, что ещё готово и когда
    он проснётся дальше. Без решений, действий и записи в журнал; кеш — 5 с."""
    try:
        view = await f.outlook()
    except PlannerUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, PLANNER_NOT_STARTED) from exc
    return outlook_out(view)
