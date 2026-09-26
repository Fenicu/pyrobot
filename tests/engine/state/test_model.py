import json
from datetime import UTC, datetime, timedelta

from app.engine.state.model import (
    SCHEMA_VERSION,
    BusyState,
    CharacterState,
    Obs,
    PriceState,
    dump_state,
    is_fresh,
    load_state,
    stale_fields,
)

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def test_roundtrip_through_json() -> None:
    state = CharacterState(
        money=Obs(value=867, at=NOW),
        busy=Obs(value=BusyState(activity="harvest", until=NOW + timedelta(minutes=5)), at=NOW),
        prices={"job": Obs(value=PriceState(motivation=1, minutes=5), at=NOW)},
        applied={"227859379:1:activity_started": NOW},
    )
    data = json.loads(json.dumps(dump_state(state)))
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["money"] == {"value": 867, "at": "2026-09-26T12:00:00Z", "src": "screen"}
    assert load_state(data) == state


def test_empty_and_incompatible_snapshots() -> None:
    assert load_state({}) == CharacterState()
    assert load_state({"events": 3}) == CharacterState()
    assert load_state({"schema_version": SCHEMA_VERSION, "money": "oops"}) == CharacterState()
    # Снимок до фазы 5: сообщение забега метро хранилось числом, а не ссылкой на забег.
    old_metro = {"value": 3625352, "at": "2026-09-26T17:05:00Z", "src": "screen"}
    snapshot = {"schema_version": SCHEMA_VERSION, "metro_message": old_metro}
    assert load_state(snapshot) == CharacterState()


def test_freshness() -> None:
    obs = Obs(value=1, at=NOW)
    assert is_fresh(obs, NOW + timedelta(minutes=14), timedelta(minutes=15))
    assert not is_fresh(obs, NOW + timedelta(minutes=16), timedelta(minutes=15))
    assert not is_fresh(None, NOW, timedelta(minutes=15))
    assert not is_fresh(Obs(value=1, at=NOW, src="doubtful"), NOW, timedelta(minutes=15))


def test_stale_fields_policy() -> None:
    old = NOW - timedelta(hours=1)
    state = CharacterState(
        money=Obs(value=1, at=old),
        level=Obs(value=71, at=old),
        books=Obs(value=5, at=NOW - timedelta(hours=7)),
        book_ready_at=Obs(value=NOW, at=NOW - timedelta(days=3)),
        stamina=Obs(value=100, at=NOW, src="doubtful"),
        prices={
            "job": Obs(value=PriceState(motivation=1), at=NOW - timedelta(days=8)),
            "learn": Obs(value=PriceState(motivation=2), at=old),
        },
    )
    assert stale_fields(state, NOW, timedelta(minutes=15)) == [
        "money",
        "stamina",
        "books",
        "prices.job",
    ]
