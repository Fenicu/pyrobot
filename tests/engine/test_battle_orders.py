from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.engine.battle_orders import (
    BATTLE_HOURS,
    BattleOrder,
    TargetChoice,
    next_battles,
    order_choice,
    target_is_set,
    targets_own_company,
)
from app.engine.gametime import MSK
from app.engine.planner.decide import _Planner
from app.engine.settings import Settings
from app.engine.state.model import CharacterState, Obs, TargetSet
from tests.engine.planner.test_obligations import msk, state

BATTLE = msk(13)
HOOLI, PIPER, UMBRELLA = "🤖Hooli", "📯Pied Piper", "☂️Umbrella"
DEFENSE = "🛡Защита"


def order(scope: str, target: str, at: datetime = BATTLE) -> BattleOrder:
    return BattleOrder(battle_at=at, scope=scope, target=target)


def test_order_corp_beats_all() -> None:
    orders = [order("all", HOOLI), order("bmesa", UMBRELLA)]
    assert order_choice(orders, "bmesa", BATTLE) == TargetChoice(UMBRELLA, "order_corp")


def test_order_all_applies_when_no_corp_order() -> None:
    orders = [order("all", HOOLI), order("bmesa", UMBRELLA)]
    assert order_choice(orders, "piper", BATTLE) == TargetChoice(HOOLI, "order_all")


def test_all_order_on_own_company_becomes_defense() -> None:
    assert order_choice([order("all", HOOLI)], "hooli", BATTLE) == TargetChoice(
        DEFENSE, "order_all"
    )


def test_corp_order_on_own_company_becomes_defense() -> None:
    choice = order_choice([order("hooli", HOOLI)], "hooli", BATTLE)
    assert choice == TargetChoice(DEFENSE, "order_corp")


def test_own_unknown_all_order_not_applied() -> None:
    assert order_choice([order("all", HOOLI)], None, BATTLE) is None


@pytest.mark.parametrize("own", ["hooli", "bmesa", "piper"])
def test_defense_order_does_not_crash(own: str) -> None:
    assert order_choice([order("all", DEFENSE)], own, BATTLE) == TargetChoice(DEFENSE, "order_all")
    assert order_choice([order(own, DEFENSE)], own, BATTLE) == TargetChoice(DEFENSE, "order_corp")


def test_defense_order_with_unknown_own_company() -> None:
    assert order_choice([order("all", DEFENSE)], None, BATTLE) is None
    assert targets_own_company(DEFENSE, "hooli") is False
    assert targets_own_company(DEFENSE, None) is False


def test_targets_own_company() -> None:
    assert targets_own_company(HOOLI, "hooli") is True
    assert targets_own_company(HOOLI, "piper") is False
    assert targets_own_company(HOOLI, None) is False


def test_order_for_other_battle_ignored() -> None:
    other = order("all", HOOLI, BATTLE + timedelta(hours=1))
    assert order_choice([other], "bmesa", BATTLE) is None


def test_no_orders() -> None:
    assert order_choice([], "bmesa", BATTLE) is None


# --- target_is_set


def planner_of(st: CharacterState, now: datetime) -> _Planner:
    return _Planner(st, Settings(), now, None, {}, {}, {})


NOW = msk(12)
SET_FOR = TargetSet(target=HOOLI, battle_at=BATTLE)
OLD_SET = TargetSet(target=HOOLI, battle_at=msk(22, day=25))


def profile(
    *,
    seen: str | None,
    known_at: datetime = NOW,
    seen_at: datetime = NOW,
    done: TargetSet | None = None,
) -> CharacterState:
    over: dict[str, Any] = {
        "battle_at": Obs(value=BATTLE, at=known_at),
        "battle_target": Obs(value=seen, at=seen_at),
    }
    if done is not None:
        over["battle_target_set"] = Obs(value=done, at=NOW)
    return state(NOW, **over)


BRANCHES = {
    "profile_equal": profile(seen=HOOLI),
    "profile_different": profile(seen=PIPER),
    "profile_older_than_battle_at_no_fallback": profile(
        seen=HOOLI, seen_at=NOW - timedelta(minutes=5)
    ),
    "profile_older_than_battle_at_fallback_ok": profile(
        seen=PIPER, seen_at=NOW - timedelta(minutes=5), done=SET_FOR
    ),
    "profile_none_fallback_ok": profile(seen=None, done=SET_FOR),
    "profile_none_fallback_other_target": profile(
        seen=None, done=TargetSet(target=PIPER, battle_at=BATTLE)
    ),
    "profile_none_fallback_other_battle": profile(seen=None, done=OLD_SET),
    "profile_none_no_fallback": profile(seen=None),
}


@pytest.mark.parametrize("name", list(BRANCHES))
def test_target_is_set_matches_target_ready(name: str) -> None:
    st = BRANCHES[name]
    ready = planner_of(st, NOW).target_ready(BATTLE, HOOLI)
    got = target_is_set(st, BATTLE, HOOLI)
    if got is None:
        assert ready is False
    else:
        assert got is ready


def test_target_is_set_unknown_for_other_battle() -> None:
    st = profile(seen=HOOLI)
    assert target_is_set(st, BATTLE + timedelta(hours=9), HOOLI) is None


def test_target_is_set_unknown_without_observations() -> None:
    assert target_is_set(CharacterState(), BATTLE, HOOLI) is None


def test_target_is_set_fallback_mismatch_on_same_battle_is_false() -> None:
    st = profile(seen=None, done=TargetSet(target=PIPER, battle_at=BATTLE))
    assert target_is_set(st, BATTLE, HOOLI) is False


def test_target_is_set_fallback_for_other_battle_unknown() -> None:
    assert target_is_set(profile(seen=None, done=OLD_SET), BATTLE, HOOLI) is None


# --- next_battles

NOW_UTC = datetime(2026, 9, 26, 7, 0, tzinfo=UTC)


def observing(*battles: datetime) -> list[CharacterState]:
    return [
        CharacterState(battle_at=Obs(value=b, at=NOW_UTC - timedelta(minutes=1))) for b in battles
    ]


def test_battle_hours_constant() -> None:
    assert BATTLE_HOURS == (13, 22)


def test_next_battles_observed() -> None:
    states = observing(msk(22), msk(13))
    assert next_battles(states, NOW_UTC) == (msk(13), msk(22))


def test_next_battles_observed_second_wraps_to_next_day() -> None:
    states = observing(msk(22))
    assert next_battles(states, NOW_UTC) == (msk(22), msk(13, day=27))


def test_next_battles_schedule_fallback() -> None:
    assert next_battles([], NOW_UTC) == (msk(13), msk(22))
    late = msk(22, 30).astimezone(UTC)
    assert next_battles([], late) == (msk(13, day=27), msk(22, day=27))
    evening = msk(14).astimezone(UTC)
    assert next_battles([], evening) == (msk(22), msk(13, day=27))


def test_next_battles_strictly_after_now() -> None:
    at_battle = msk(13).astimezone(UTC)
    assert next_battles([], at_battle) == (msk(22), msk(13, day=27))


def test_next_battles_hours_changed() -> None:
    states = observing(datetime(2026, 9, 26, 18, tzinfo=MSK))
    first, second = next_battles(states, NOW_UTC)
    assert first == datetime(2026, 9, 26, 18, tzinfo=MSK)
    assert second is None


def test_next_battles_ignores_stale_or_past() -> None:
    states = observing(msk(10), msk(22))
    assert next_battles(states, NOW_UTC) == (msk(22), msk(13, day=27))


def test_next_battles_all_past_falls_back_to_schedule() -> None:
    assert next_battles(observing(msk(10)), NOW_UTC) == (msk(13), msk(22))
