import pytest
from sqlalchemy import delete, select

from app.db.base import Database
from app.db.models import RecoveryCodeRow, User
from app.db.recovery import RecoveryCodes, format_code, new_secret, parse_code

pytestmark = pytest.mark.db


def test_format_and_parse_roundtrip() -> None:
    assert parse_code("412-k7qm2-xh9td") == (412, "K7QM2XH9TD")
    assert parse_code("  412 - K7QM2 - XH9TD  ") == (412, "K7QM2XH9TD")
    assert parse_code("412-k7qm2xh9td") == (412, "K7QM2XH9TD")
    assert parse_code("412--k7qm2---xh9td") == (412, "K7QM2XH9TD")

    secret = "K7QM2XH9TD"
    formatted = format_code(412, secret)
    assert formatted == "412-K7QM2-XH9TD"
    assert parse_code(formatted) == (412, secret)

    # Invalid codes
    assert parse_code("invalid") is None
    assert parse_code("-k7qm2-xh9td") is None
    assert parse_code("abc-k7qm2-xh9td") is None
    assert parse_code("412-k7qm!-xh9td") is None  # '!' is not alphanumeric
    assert parse_code("412-k7qm2-xh9t") is None  # too short
    assert parse_code("412-k7qm2-xh9tdd") is None  # too long
    assert parse_code("") is None


def test_new_secret() -> None:
    s = new_secret()
    assert len(s) == 10
    assert s.isupper() or s.isalnum()
    assert set(s) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


async def _create_user(db: Database, login: str) -> int:
    async with db.sessions() as s, s.begin():
        u = User(login=login, password_hash="hash", role="user", max_accounts=1)
        s.add(u)
        await s.flush()
        return u.id


async def test_use_is_one_time(clean_db: Database) -> None:
    user_id = await _create_user(clean_db, "user1")
    rc = RecoveryCodes(clean_db)
    codes = await rc.issue(user_id)
    assert len(codes) == 10

    code = codes[0]
    assert await rc.use(user_id, code) is True
    assert await rc.use(user_id, code) is False


async def test_code_of_other_user_rejected(clean_db: Database) -> None:
    u1 = await _create_user(clean_db, "user1")
    u2 = await _create_user(clean_db, "user2")
    rc = RecoveryCodes(clean_db)
    codes1 = await rc.issue(u1)
    await rc.issue(u2)

    assert await rc.use(u2, codes1[0]) is False
    assert await rc.use(u1, codes1[0]) is True


async def test_reissue_invalidates_previous(clean_db: Database) -> None:
    user_id = await _create_user(clean_db, "user1")
    rc = RecoveryCodes(clean_db)
    first_codes = await rc.issue(user_id)
    second_codes = await rc.issue(user_id)

    for c in first_codes:
        assert await rc.use(user_id, c) is False

    assert await rc.use(user_id, second_codes[0]) is True


async def test_use_calls_argon2_once(clean_db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.db.recovery as rec_mod

    user_id = await _create_user(clean_db, "user1")
    rc = RecoveryCodes(clean_db)
    codes = await rc.issue(user_id)
    valid_code = codes[0]

    verify_calls = 0
    orig_verify = rec_mod.verify_password

    async def counting_verify(h: str, p: str) -> bool:
        nonlocal verify_calls
        verify_calls += 1
        return await orig_verify(h, p)

    monkeypatch.setattr(rec_mod, "verify_password", counting_verify)

    # 1. Unparseable code -> exactly 1 call (dummy hash)
    verify_calls = 0
    assert await rc.use(user_id, "garbage") is False
    assert verify_calls == 1

    # 2. Non-existent row_id -> exactly 1 call (dummy hash)
    verify_calls = 0
    fake_code = "999999-AAAAA-BBBBB"
    assert await rc.use(user_id, fake_code) is False
    assert verify_calls == 1

    # 3. Wrong user_id for existing row -> exactly 1 call (dummy hash)
    verify_calls = 0
    assert await rc.use(999999, valid_code) is False
    assert verify_calls == 1

    # 4. Correct row_id, but wrong secret -> exactly 1 call
    parsed = parse_code(valid_code)
    assert parsed is not None
    row_id, _ = parsed
    wrong_secret_code = format_code(row_id, "2222233333")
    verify_calls = 0
    assert await rc.use(user_id, wrong_secret_code) is False
    assert verify_calls == 1

    # 5. Valid code -> exactly 1 call
    verify_calls = 0
    assert await rc.use(user_id, valid_code) is True
    assert verify_calls == 1


async def test_user_delete_cascades_codes(clean_db: Database) -> None:
    user_id = await _create_user(clean_db, "user_del")
    rc = RecoveryCodes(clean_db)
    codes = await rc.issue(user_id)
    assert len(codes) == 10

    async with clean_db.sessions() as s:
        rows = list(
            await s.scalars(select(RecoveryCodeRow).where(RecoveryCodeRow.user_id == user_id))
        )
        assert len(rows) == 10

    async with clean_db.sessions() as s, s.begin():
        await s.execute(delete(User).where(User.id == user_id))

    async with clean_db.sessions() as s:
        rows = list(
            await s.scalars(select(RecoveryCodeRow).where(RecoveryCodeRow.user_id == user_id))
        )
        assert len(rows) == 0
