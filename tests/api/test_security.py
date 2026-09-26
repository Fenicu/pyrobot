from app.api.security import LoginRateLimiter, hash_password, token_hash, verify_password


async def test_hash_and_verify() -> None:
    h = await hash_password("correct horse battery")
    assert await verify_password(h, "correct horse battery")
    assert not await verify_password(h, "wrong")
    assert not await verify_password("garbage", "x")


def test_token_hash_is_stable() -> None:
    assert token_hash("a") == token_hash("a") and len(token_hash("a")) == 64


def test_rate_limiter_backoff() -> None:
    t = [0.0]
    rl = LoginRateLimiter(free_attempts=2, base_s=10, max_s=40, clock=lambda: t[0])
    rl.failure("ip")
    rl.failure("ip")
    assert rl.blocked_for("ip") == 0
    rl.failure("ip")
    assert rl.blocked_for("ip") == 10
    t[0] = 11
    assert rl.blocked_for("ip") == 0
    rl.failure("ip")
    assert rl.blocked_for("ip") == 20
    rl.success("ip")
    assert rl.blocked_for("ip") == 0
    assert rl.lock_for("ip") is rl.lock_for("ip")


def test_rate_limiter_forgets_entries_older_than_window() -> None:
    t = [0.0]
    rl = LoginRateLimiter(free_attempts=1, base_s=10, max_s=40, window_s=100, clock=lambda: t[0])
    rl.failure("ip")
    rl.failure("ip")
    assert rl.blocked_for("ip") == 10
    t[0] = 50
    rl.failure("ip")
    assert rl.blocked_for("ip") == 20
    t[0] = 50 + 20 + 101
    assert rl.blocked_for("ip") == 0
    assert rl.tracked == 0
    rl.failure("ip")
    assert rl.blocked_for("ip") == 0


def test_rate_limiter_sweeps_stale_entries_over_capacity() -> None:
    t = [0.0]
    rl = LoginRateLimiter(window_s=100, max_entries=2, clock=lambda: t[0])
    for key in ("a", "b", "c"):
        rl.lock_for(key)
        rl.failure(key)
    t[0] = 200
    rl.lock_for("d")
    rl.failure("d")
    assert rl.tracked == 1
