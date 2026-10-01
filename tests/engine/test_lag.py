import logging

import pytest

from app.engine.lag import LoopLagMonitor


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_lag_over_second_warns_at_most_once_a_minute(caplog: pytest.LogCaptureFixture) -> None:
    lag = LoopLagMonitor()
    with caplog.at_level(logging.WARNING, logger="app.engine.lag"):
        lag.observe(10.0, 1000.0)
        assert _warnings(caplog) == []
        lag.observe(11.0, 1500.0)
        [first] = _warnings(caplog)
        assert "1500 мс" in first
        # В пределах минуты — не чаще: подавленные считаются.
        lag.observe(40.0, 2000.0)
        lag.observe(70.9, 1200.0)
        lag.observe(70.95, 300.0)
        assert len(_warnings(caplog)) == 1
        lag.observe(71.0, 1100.0)
        second = _warnings(caplog)[1]
        assert "1100 мс" in second and "ещё 2" in second
        lag.observe(131.0, 1300.0)
        third = _warnings(caplog)[2]
        assert "1300 мс" in third and "ещё" not in third
