"""Итог поездки «сюжет + Ты получил:» распознаётся условно: без идущей поездки сообщение —
нераспознанное (лента, всплеск, метро), с поездкой — ряд `trip` в журнале прихода."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.engine.bus import Bus
from app.engine.events import Unrecognized
from app.engine.memory import MemoryJournal
from app.engine.parsing import default_parser
from app.engine.parsing.trips import RewardsOnly
from app.engine.pipeline import Pipeline
from app.engine.settings import ChatsSection
from app.engine.state.reducer import StateReducer
from tests.engine import trip_texts as t
from tests.engine.artifact_texts import game_text

T0 = datetime(2026, 10, 4, 0, 11, 21, tzinfo=UTC)


def _pipeline() -> tuple[Pipeline, MemoryJournal]:
    journal = MemoryJournal()
    pipe = Pipeline(
        journal=journal,
        parser=default_parser(ChatsSection()),
        reducer=StateReducer(),
        bus=Bus(),
        retry_base_s=0.01,
    )
    return pipe, journal


async def test_rewards_only_without_trip_is_unrecognized() -> None:
    pipe, journal = _pipeline()
    delivery = await pipe.process(game_text(t.RESULT_TRAM_EXP, at=T0, msg_id=10))
    assert delivery is not None
    first = "Пока трамвай бодро бежал по рельсам, тебе удалось поразмыслить о высоком."
    assert [type(e) for e in delivery.events] == [RewardsOnly, Unrecognized]
    assert delivery.events[-1] == Unrecognized(first_line=first)
    assert journal.unrecognized == [(delivery.journal_id, first)]
    assert journal.ledger == []
    # Не итог — состояние не меняется, версия снимка не растёт.
    assert pipe.version == 0 and journal.snapshot == ({}, 0)


async def test_trip_result_goes_to_ledger_not_unrecognized() -> None:
    pipe, journal = _pipeline()
    await pipe.process(game_text(t.START_BIKE, at=T0, msg_id=1))
    later = T0 + timedelta(minutes=10)
    delivery = await pipe.process(game_text(t.RESULT_BIKE, at=later, msg_id=2))
    assert delivery is not None
    assert not any(isinstance(e, Unrecognized) for e in delivery.events)
    assert journal.unrecognized == []
    [(_, effect, _)] = [row for row in journal.ledger if row[1].kind == "trip"]
    assert (effect.amounts, effect.key) == ({"knowledge": 16}, f"trip:{T0.isoformat()}")
    # Правка того же итога — тоже не нераспознанное и не второй ряд.
    edit = replace(
        game_text(t.RESULT_BIKE, at=later + timedelta(seconds=5), msg_id=2),
        revision=1,
        created_at=later,
    )
    delivery = await pipe.process(edit)
    assert delivery is not None
    assert not any(isinstance(e, Unrecognized) for e in delivery.events)
    assert [row[1].kind for row in journal.ledger].count("trip") == 1


async def test_bonus_only_trip_result_is_a_ledger_row() -> None:
    pipe, journal = _pipeline()
    await pipe.process(game_text(t.START_CAR, at=T0, msg_id=1))
    later = T0 + timedelta(minutes=10)
    delivery = await pipe.process(game_text(t.RESULT_CAR_BONUS, at=later, msg_id=2))
    assert delivery is not None
    assert not any(isinstance(e, Unrecognized) for e in delivery.events)
    assert [(row[1].kind, row[1].amounts) for row in journal.ledger][-1] == ("trip", {})
