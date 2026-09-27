from app.api.confirm import ConfirmTokens


def test_token_bound_to_session_key_params_and_version() -> None:
    t = [1000.0]
    tokens = ConfirmTokens(secret=b"s" * 32, ttl_s=60, clock=lambda: t[0])
    params = ("send", 1, "/ucon")
    tok = tokens.issue(7, "k", params, 3)
    assert tokens.check(tok, 7, "k", params, 3) is None
    assert tokens.check(tok, 8, "k", params, 3) == "invalid"
    assert tokens.check(tok, 7, "k2", params, 3) == "invalid"
    assert tokens.check(tok, 7, "k", ("send", 1, "/v_ant"), 3) == "invalid"
    assert tokens.check(tok, 7, "k", params, 4) == "invalid"
    assert tokens.check("garbage", 7, "k", params, 3) == "invalid"
    assert tokens.check("1060.", 7, "k", params, 3) == "invalid"
    t[0] = 1061.0
    assert tokens.check(tok, 7, "k", params, 3) == "expired"


def test_tokens_die_with_process_secret() -> None:
    params = ("send", 1, "/ucon")
    tok = ConfirmTokens().issue(1, "k", params, 0)
    assert ConfirmTokens().check(tok, 1, "k", params, 0) == "invalid"
