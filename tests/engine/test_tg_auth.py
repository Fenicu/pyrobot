import asyncio

import pytest

from app.engine.tg_auth import (
    AttemptMismatch,
    InvalidPhone,
    TgAuthManager,
    TgBackendError,
    TgState,
)
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


class _RaisingGoOnline(FakeTgBackend):
    async def go_online(self) -> None:
        raise RuntimeError("boom")


class _RaisingLogOut(FakeTgBackend):
    async def log_out(self) -> None:
        raise RuntimeError("boom")


async def test_go_online_failure_reports_online_failed_without_raising() -> None:
    mgr = TgAuthManager(_RaisingGoOnline(), expected_user_id=EXPECTED)
    await mgr.boot()
    st = await mgr.start("+888", owner="s1")
    st = await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.ERROR and st.error == "online_failed"


async def test_foreign_user_log_out_failure_still_reports_unexpected_user() -> None:
    backend = _RaisingLogOut(user_id=42)
    mgr = TgAuthManager(backend, expected_user_id=EXPECTED)
    await mgr.boot()
    st = await mgr.start("+888", owner="s1")
    st = await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.ERROR and st.error == "unexpected_user"
    assert not backend.online


async def test_logout_failure_reports_logout_failed_without_raising() -> None:
    backend = _RaisingLogOut(authorized=True)
    mgr = TgAuthManager(backend, expected_user_id=EXPECTED)
    await mgr.boot()
    st = await mgr.logout()
    assert st.state is TgState.ERROR and st.error == "logout_failed"


class _Recorder:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    async def notify(self, level: str, code: str, text: str) -> None:
        self.items.append((level, code))


async def test_mark_lost_notifies_once_per_online_session() -> None:
    rec = _Recorder()
    mgr = TgAuthManager(FakeTgBackend(authorized=True), expected_user_id=EXPECTED, notifier=rec)
    await mgr.mark_lost()
    assert rec.items == []
    await mgr.boot()
    await mgr.mark_lost()
    await mgr.mark_lost()
    assert rec.items == [("error", "tg_auth_lost")]
    await mgr.boot()
    await mgr.mark_lost()
    assert rec.items == [("error", "tg_auth_lost")] * 2


class _SendCodeDown(FakeTgBackend):
    async def send_code(self, phone: str) -> str:
        raise ConnectionError("network down")


class _BadPhone(FakeTgBackend):
    async def send_code(self, phone: str) -> str:
        raise InvalidPhone


class _SignInDown(FakeTgBackend):
    async def sign_in(self, phone: str, code_hash: str, code: str) -> int:
        raise ConnectionError("network down")


async def test_start_backend_failure_sets_error_and_raises() -> None:
    mgr = TgAuthManager(_SendCodeDown(), expected_user_id=EXPECTED)
    await mgr.boot()
    with pytest.raises(TgBackendError) as info:
        await mgr.start("+888", owner="s1")
    assert info.value.code == "send_code_failed"
    st = mgr.status()
    assert st.state is TgState.ERROR and st.error == "send_code_failed" and st.attempt_id is None


async def test_start_classified_error_keeps_code() -> None:
    mgr = TgAuthManager(_BadPhone(), expected_user_id=EXPECTED)
    await mgr.boot()
    with pytest.raises(InvalidPhone):
        await mgr.start("+888", owner="s1")
    assert mgr.status().state is TgState.ERROR and mgr.status().error == "invalid_phone"


async def test_sign_in_backend_failure_keeps_attempt() -> None:
    mgr = TgAuthManager(_SignInDown(), expected_user_id=EXPECTED)
    await mgr.boot()
    st = await mgr.start("+888", owner="s1")
    with pytest.raises(TgBackendError) as info:
        await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert info.value.code == "sign_in_failed"
    assert mgr.status().state is TgState.AWAITING_CODE
