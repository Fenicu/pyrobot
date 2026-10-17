"""Обзор аккаунтов учётки: расписание ближайших часов (состояние, планировщик и общие отметки)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.container import Container
from app.api.deps import SessionContext, container, current_session
from app.api.errors import AUTH
from app.api.overview_battle import fetch_plans
from app.api.routes_engine import IsoDatetime
from app.db.overview import AccountInput
from app.engine.battle_orders import next_battles
from app.engine.gametime import MSK, to_msk
from app.engine.planner.loop import PlanView
from app.engine.planner.obligations import (
    FACTORY_BATTLE,
    FACTORY_OPEN,
    LOTTERY_OPEN,
    NIGHT_END,
    NIGHT_START,
)
from app.engine.state.model import CharacterState

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])

# Таймеры планировщика, которых нет в состоянии аккаунта: показываются в расписании
PLANNER_KINDS_SHOWN: frozenset[str] = frozenset(
    {
        "cooldown",
        "refresh",
        "sleep_window",
        "gorbushka_next",
        "gorbushka_comeback",
        "motivation",
        "daily_midnight",
        "daily_reset",
        "stocks_dump",
        "factory_open",
        "factory_report",
        "tangerine_not_player",
        "lottery_open",
        "metro_kick",
        "metro_probe",
        "trip_ready",
        "trip_result",
        "market_open",
        "gear_guard",
    }
)


class SharedScheduleItemOut(BaseModel):
    kind: Literal["battle", "factory", "lottery", "night"]
    at: IsoDatetime
    until: IsoDatetime | None = None
    label: str


class ScheduleItemOut(BaseModel):
    kind: str
    at: IsoDatetime
    until: IsoDatetime | None = None
    source: Literal["state", "planner"]
    detail: str | None = None


class AccountScheduleOut(BaseModel):
    id: int
    seen_at: IsoDatetime | None
    stale: bool
    items: list[ScheduleItemOut]


class ScheduleOut(BaseModel):
    now: IsoDatetime
    until: IsoDatetime
    shared: list[SharedScheduleItemOut]
    accounts: list[AccountScheduleOut]


def _seen_at(state: CharacterState | None) -> datetime | None:
    if state is None:
        return None
    timestamps = [
        obs.at.astimezone(UTC)
        for obs in (state.level, state.money)
        if obs is not None and obs.at is not None
    ]
    return max(timestamps) if timestamps else None


def _is_stale(seen_at: datetime | None, stale_min: int, now: datetime) -> bool:
    if seen_at is None:
        return True
    return (now - seen_at) > timedelta(minutes=stale_min)


def _shared_marks(
    inputs: Sequence[AccountInput], now: datetime, until: datetime
) -> list[SharedScheduleItemOut]:
    shared: list[SharedScheduleItemOut] = []

    # 1. Битвы: по наблюдениям или по расписанию (next_battles)
    states = [i.state for i in inputs if i.state is not None]
    first, second = next_battles(states, now)
    for b in (first, second):
        if b is not None and now <= b <= until:
            b_utc = b.astimezone(UTC)
            shared.append(
                SharedScheduleItemOut(
                    kind="battle",
                    at=b_utc,
                    until=None,
                    label=to_msk(b).strftime("%H:%M"),
                )
            )

    # 2. Окна по суткам МСК: фабрика, лотерея, ночь
    now_msk = to_msk(now)
    until_msk = to_msk(until)
    days = [
        now_msk.date() + timedelta(days=d)
        for d in range(-1, (until_msk.date() - now_msk.date()).days + 2)
    ]

    for day in days:
        # Фабрика: 18:00 - 18:30 МСК
        f_start = datetime(
            day.year, day.month, day.day, FACTORY_OPEN.hour, FACTORY_OPEN.minute, tzinfo=MSK
        ).astimezone(UTC)
        f_end = datetime(
            day.year, day.month, day.day, FACTORY_BATTLE.hour, FACTORY_BATTLE.minute, tzinfo=MSK
        ).astimezone(UTC)
        if f_end > now and f_start < until:
            shared.append(
                SharedScheduleItemOut(
                    kind="factory",
                    at=f_start,
                    until=f_end,
                    label="18:00–18:30",
                )
            )

        # Лотерея: 19:17 - 21:07 МСК
        l_start = datetime(
            day.year, day.month, day.day, LOTTERY_OPEN.hour, LOTTERY_OPEN.minute, tzinfo=MSK
        ).astimezone(UTC)
        l_end = datetime(day.year, day.month, day.day, 21, 7, tzinfo=MSK).astimezone(UTC)
        if l_end > now and l_start < until:
            shared.append(
                SharedScheduleItemOut(
                    kind="lottery",
                    at=l_start,
                    until=l_end,
                    label="19:17–21:07",
                )
            )

        # Ночь: 22:00 МСК - 08:00 МСК следующего дня
        n_start = datetime(
            day.year, day.month, day.day, NIGHT_START.hour, NIGHT_START.minute, tzinfo=MSK
        ).astimezone(UTC)
        next_day = day + timedelta(days=1)
        n_end = datetime(
            next_day.year,
            next_day.month,
            next_day.day,
            NIGHT_END.hour,
            NIGHT_END.minute,
            tzinfo=MSK,
        ).astimezone(UTC)
        if n_end > now and n_start < until:
            shared.append(
                SharedScheduleItemOut(
                    kind="night",
                    at=n_start,
                    until=n_end,
                    label="22:00–08:00",
                )
            )

    shared.sort(key=lambda s: (s.at, s.kind))
    return shared


def _account_items(
    inp: AccountInput,
    plan: PlanView | None,
    now: datetime,
    until: datetime,
) -> list[ScheduleItemOut]:
    items: list[ScheduleItemOut] = []
    state = inp.state

    if state is not None:
        # 1. Занятость / сон (отрезки)
        if state.busy is not None and state.busy.value is not None:
            act = state.busy.value.activity
            kind = "sleep" if act.startswith("sleep_") else "busy"
            b_at = state.busy.at.astimezone(UTC)
            b_until = state.busy.value.until.astimezone(UTC)
            if b_until > now and b_at <= until:
                items.append(
                    ScheduleItemOut(
                        kind=kind,
                        at=b_at,
                        until=b_until,
                        source="state",
                        detail=act,
                    )
                )

        # 2. Метки состояния (until is None)
        art_ends = (
            state.artifact_collect.value.ends_at
            if state.artifact_collect is not None and state.artifact_collect.value is not None
            else None
        )
        art_name = (
            state.artifact_collect.value.artifact
            if state.artifact_collect is not None and state.artifact_collect.value is not None
            else None
        )
        lottery_close = (
            state.lottery.value.until
            if state.lottery is not None and state.lottery.value is not None
            else None
        )
        lottery_draw = (
            str(state.lottery.value.draw)
            if state.lottery is not None and state.lottery.value is not None
            else None
        )

        marks: list[tuple[str, datetime | None, str | None]] = [
            (
                "sleep_deadline",
                state.sleep_deadline.value if state.sleep_deadline is not None else None,
                None,
            ),
            (
                "sleep_allowed",
                state.sleep_allowed_at.value if state.sleep_allowed_at is not None else None,
                None,
            ),
            (
                "book_ready",
                state.book_ready_at.value if state.book_ready_at is not None else None,
                None,
            ),
            (
                "card_ready",
                state.card_ready_at.value if state.card_ready_at is not None else None,
                None,
            ),
            (
                "fastfood_ready",
                state.fastfood_ready_at.value if state.fastfood_ready_at is not None else None,
                None,
            ),
            (
                "prizebox_ready",
                state.prizebox_ready_at.value if state.prizebox_ready_at is not None else None,
                None,
            ),
            (
                "tangerine_ready",
                state.tangerine_ready_at.value if state.tangerine_ready_at is not None else None,
                None,
            ),
            (
                "metro_ready",
                state.metro_ready_at.value if state.metro_ready_at is not None else None,
                None,
            ),
            ("artifact_end", art_ends, art_name),
            ("lottery_close", lottery_close, lottery_draw),
        ]

        for m_kind, m_at, m_detail in marks:
            if m_at is not None:
                m_utc = m_at.astimezone(UTC)
                # Метки в прошлом отдаются как есть (клиент прижимает влево);
                # из будущего — только в границах окна
                if m_utc <= until:
                    items.append(
                        ScheduleItemOut(
                            kind=m_kind,
                            at=m_utc,
                            until=None,
                            source="state",
                            detail=m_detail,
                        )
                    )

    # 3. Таймеры планировщика (только для локального движка)
    if plan is not None:
        wakeups = plan.outlook.wakeups + plan.outlook.after_wake
        for w in wakeups:
            if w.kind in PLANNER_KINDS_SHOWN:
                w_utc = w.at.astimezone(UTC)
                if w_utc <= until:
                    items.append(
                        ScheduleItemOut(
                            kind=w.kind,
                            at=w_utc,
                            until=None,
                            source="planner",
                            detail=w.key,
                        )
                    )

    items.sort(key=lambda it: (it.at, it.kind))
    return items


@router.get("/schedule", response_model=ScheduleOut, responses=AUTH)
async def schedule(
    ctx: Annotated[SessionContext, Depends(current_session)],
    c: Annotated[Container, Depends(container)],
    hours: int = Query(12, ge=1, le=24),
) -> ScheduleOut:
    """Расписание ближайших часов: занятость, таймеры и общие события."""
    now = c.clock.now()
    until = now + timedelta(hours=hours)
    inputs = await c.overview.account_inputs(ctx.user_id)

    # Планировщик читаем только у движков, запущенных в этом процессе
    local_runtimes = {
        i.info.id: c.engines.get(i.info.id) for i in inputs if c.engines.get(i.info.id) is not None
    }
    plans = await fetch_plans(local_runtimes)

    accounts_out: list[AccountScheduleOut] = []
    for inp in inputs:
        state = inp.state
        seen_at = _seen_at(state)
        stale = _is_stale(seen_at, inp.settings.engine.state_stale_after_min, now)
        items = _account_items(inp, plans.get(inp.info.id), now, until)
        accounts_out.append(
            AccountScheduleOut(
                id=inp.info.id,
                seen_at=seen_at,
                stale=stale,
                items=items,
            )
        )

    shared = _shared_marks(inputs, now, until)
    return ScheduleOut(
        now=now,
        until=until,
        shared=shared,
        accounts=accounts_out,
    )
