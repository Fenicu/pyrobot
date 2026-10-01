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
