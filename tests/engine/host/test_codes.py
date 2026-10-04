from app.engine.host.codes import CodeLimiter
from tests.engine.test_fence import FakeMonotonic


def test_three_per_account_ten_per_host_per_hour() -> None:
    clock = FakeMonotonic(0.0)
    codes = CodeLimiter(10, monotonic=clock)
    for _ in range(3):
        assert codes.take(1) is None
        clock.now += 1.0
    # Четвёртый за час у аккаунта — отказ до истечения часа с первого; отказ квоту не тратит.
    assert codes.take(1) == 3600.0 - 3.0
    assert codes.take(1) == 3600.0 - 3.0
    for account in (2, 3):
        for _ in range(3):
            assert codes.take(account) is None
    assert codes.take(4) is None
    # Десятый на хост был последним: у аккаунта 4 место есть, у хоста — нет.
    clock.now = 10.0
    assert codes.take(4) == 3600.0 - 10.0
    assert codes.take(5) == 3600.0 - 10.0
    # Час с первого запроса: место освободилось и у хоста, и у аккаунта 1.
    clock.now = 3600.0
    assert codes.take(1) is None
    assert codes.take(5) == 1.0
    clock.now = 3601.0
    assert codes.take(5) is None
    assert codes.take(1) == 1.0


def test_refusals_do_not_extend_the_wait() -> None:
    clock = FakeMonotonic(0.0)
    codes = CodeLimiter(10, per_account=1, window_s=60.0, monotonic=clock)
    assert codes.take(1) is None
    for second in range(1, 60):
        clock.now = float(second)
        assert codes.take(1) == 60.0 - second
    clock.now = 60.0
    assert codes.take(1) is None


def test_limits_read_on_each_take() -> None:
    current_limits = [10, 3]
    codes = CodeLimiter(lambda: (current_limits[0], current_limits[1]))
    assert codes.take(1) is None
    assert codes.take(1) is None
    # Tighten account limit to 2
    current_limits[1] = 2
    assert codes.take(1) is not None
    # Relax back to 3
    current_limits[1] = 3
    assert codes.take(1) is None


def test_host_limit_callback_once_per_window() -> None:
    clock = FakeMonotonic(0.0)
    calls: list[float] = []

    def on_host_limit() -> None:
        calls.append(clock.now)

    codes = CodeLimiter(
        lambda: (2, 2),
        on_host_limit=on_host_limit,
        window_s=60.0,
        monotonic=clock,
    )
    assert codes.take(1) is None
    assert codes.take(2) is None
    # Next take hits host limit (2 reached)
    assert codes.take(3) is not None
    assert calls == [0.0]

    # Repeated take within window does NOT invoke callback again
    clock.now = 10.0
    assert codes.take(3) is not None
    assert calls == [0.0]

    # After window passes, takes are admitted again
    clock.now = 61.0
    assert codes.take(1) is None
    assert codes.take(2) is None
    # Hit host limit again in new window
    assert codes.take(3) is not None
    assert calls == [0.0, 61.0]
