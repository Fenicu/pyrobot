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
