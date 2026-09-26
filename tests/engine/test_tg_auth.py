import asyncio

import pytest

from app.engine.tg_auth import AttemptMismatch, TgAuthManager, TgState
from app.engine.transport.fake import FakeTgBackend

EXPECTED = 267519921


async def test_boot_online_when_authorized() -> None:
    backend = FakeTgBackend(authorized=True)
    mgr = TgAuthManager(backend, expected_user_id=EXPECTED)
    status = await mgr.boot()
    assert status.state is TgState.ONLINE and status.user_id == EXPECTED and backend.online


async def test_boot_foreign_session_logged_out_before_updates() -> None:
    backend = FakeTgBackend(authorized=True, user_id=42)
    mgr = TgAuthManager(backend, expected_user_id=EXPECTED)
    status = await mgr.boot()
    assert status.state is TgState.ERROR and status.error == "unexpected_user"
    assert backend.logged_out and not backend.online


async def test_code_flow_and_online_callback() -> None:
    mgr = TgAuthManager(FakeTgBackend(), expected_user_id=EXPECTED)
    called: list[str] = []

    async def cb() -> None:
        called.append("online")

    mgr.on_online(cb)
    assert (await mgr.boot()).state is TgState.UNAUTHORIZED
    st = await mgr.start("+888", owner="s1")
    assert st.state is TgState.AWAITING_CODE and st.attempt_id
    bad = await mgr.submit_code(st.attempt_id, "s1", "00000")
    assert bad.state is TgState.AWAITING_CODE and bad.error == "invalid_code"
    ok = await mgr.submit_code(st.attempt_id, "s1", "12345")
    assert ok.state is TgState.ONLINE and called == ["online"]


async def test_password_flow() -> None:
    mgr = TgAuthManager(FakeTgBackend(password="pw"), expected_user_id=EXPECTED)
    await mgr.boot()
    st = await mgr.start("+888", owner="s1")
    st = await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.AWAITING_PASSWORD
    assert (
        await mgr.submit_password(st.attempt_id or "", "s1", "nope")
    ).error == "invalid_password"
    assert (await mgr.submit_password(st.attempt_id or "", "s1", "pw")).state is TgState.ONLINE


async def test_attempt_bound_to_owner_and_not_hijacked() -> None:
    mgr = TgAuthManager(FakeTgBackend(), expected_user_id=EXPECTED, attempt_ttl_s=0.05)
    await mgr.boot()
    st = await mgr.start("+888", owner="s1")
    with pytest.raises(AttemptMismatch):
        await mgr.submit_code(st.attempt_id or "", "s2", "12345")
    with pytest.raises(AttemptMismatch):
        await mgr.submit_code("other", "s1", "12345")
    with pytest.raises(AttemptMismatch):
        await mgr.start("+888", owner="s2")
    again = await mgr.start("+888", owner="s1")
    assert again.attempt_id != st.attempt_id
    await asyncio.sleep(0.06)
    assert (await mgr.start("+888", owner="s2")).state is TgState.AWAITING_CODE


async def test_unexpected_user_after_code_logged_out() -> None:
    backend = FakeTgBackend(user_id=42)
    mgr = TgAuthManager(backend, expected_user_id=EXPECTED)
    await mgr.boot()
    st = await mgr.start("+888", owner="s1")
    st = await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.ERROR and st.error == "unexpected_user"
    assert backend.logged_out and not backend.online


async def test_mark_lost() -> None:
    mgr = TgAuthManager(FakeTgBackend(authorized=True), expected_user_id=EXPECTED)
    await mgr.boot()
    await mgr.mark_lost()
    st = mgr.status()
    assert st.state is TgState.UNAUTHORIZED and st.error == "session_revoked"
