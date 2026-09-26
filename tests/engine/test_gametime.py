from datetime import UTC, datetime

from app.engine.gametime import MSK, from_msk_naive, to_msk


def test_msk_roundtrip() -> None:
    moment = from_msk_naive(datetime(2026, 9, 26, 1, 55, 49))
    assert moment == datetime(2026, 9, 25, 22, 55, 49, tzinfo=UTC)
    assert to_msk(moment).hour == 1
    assert to_msk(moment).utcoffset() == MSK.utcoffset(None)
