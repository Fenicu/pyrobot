import asyncio

import pytest

from app.engine.host.codes import CodeLimiter
from app.engine.settings import ChatsSection, Settings, self_chat_fields
from app.engine.tg_auth import (
    AttemptMismatch,
    CodeRateLimited,
    InvalidPhone,
    TgBackendError,
    TgState,
    TgUserTaken,
)
from app.engine.transport.base import TransportAuthLost
from app.engine.transport.fake import FakeTgBackend
from app.engine.types import IncomingMessage
from tests.engine.helpers import GAME, tg_auth, until
from tests.engine.kurigram_fakes import FakeKurigram
from tests.engine.test_fence import FakeMonotonic
from tests.engine.test_kurigram_transport import _game_message

EXPECTED = 267519921


async def test_boot_online_when_authorized() -> None:
    backend = FakeTgBackend(authorized=True)
    mgr = tg_auth(backend, expected_user_id=EXPECTED)
    status = await mgr.boot()
    assert status.state is TgState.ONLINE and status.user_id == EXPECTED and backend.online


async def test_boot_foreign_session_logged_out_before_updates() -> None:
    backend = FakeTgBackend(authorized=True, user_id=42)
    mgr = tg_auth(backend, expected_user_id=EXPECTED)
    status = await mgr.boot()
    assert status.state is TgState.ERROR and status.error == "unexpected_user"
    assert backend.logged_out and not backend.online


async def test_code_flow_and_online_callback() -> None:
    mgr = tg_auth(FakeTgBackend(), expected_user_id=EXPECTED)
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
    mgr = tg_auth(FakeTgBackend(password="pw"), expected_user_id=EXPECTED)
    await mgr.boot()
    st = await mgr.start("+888", owner="s1")
    st = await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.AWAITING_PASSWORD
    assert (
        await mgr.submit_password(st.attempt_id or "", "s1", "nope")
    ).error == "invalid_password"
    assert (await mgr.submit_password(st.attempt_id or "", "s1", "pw")).state is TgState.ONLINE


async def test_attempt_bound_to_owner_and_not_hijacked() -> None:
    mgr = tg_auth(FakeTgBackend(), expected_user_id=EXPECTED, attempt_ttl_s=0.05)
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
    mgr = tg_auth(backend, expected_user_id=EXPECTED)
    await mgr.boot()
    st = await mgr.start("+888", owner="s1")
    st = await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.ERROR and st.error == "unexpected_user"
    assert backend.logged_out and not backend.online


class _RevokedAtBoot(FakeTgBackend):
    async def identify(self) -> int:
        raise TransportAuthLost("session revoked")


async def test_boot_session_revoked_while_stopped_reports_and_unauthorized() -> None:
    rec = _Recorder()
    mgr = tg_auth(_RevokedAtBoot(authorized=True), expected_user_id=EXPECTED, notifier=rec)
    st = await mgr.boot()
    assert st.state is TgState.UNAUTHORIZED and st.error == "session_revoked"
    assert rec.items == [("error", "tg_auth_lost")]


async def test_mark_lost() -> None:
    mgr = tg_auth(FakeTgBackend(authorized=True), expected_user_id=EXPECTED)
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
    mgr = tg_auth(_RaisingGoOnline(), expected_user_id=EXPECTED)
    await mgr.boot()
    st = await mgr.start("+888", owner="s1")
    st = await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.ERROR and st.error == "online_failed"


async def test_foreign_user_log_out_failure_still_reports_unexpected_user() -> None:
    backend = _RaisingLogOut(user_id=42)
    mgr = tg_auth(backend, expected_user_id=EXPECTED)
    await mgr.boot()
    st = await mgr.start("+888", owner="s1")
    st = await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.ERROR and st.error == "unexpected_user"
    assert not backend.online


async def test_logout_failure_reports_logout_failed_without_raising() -> None:
    backend = _RaisingLogOut(authorized=True)
    mgr = tg_auth(backend, expected_user_id=EXPECTED)
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
    mgr = tg_auth(FakeTgBackend(authorized=True), expected_user_id=EXPECTED, notifier=rec)
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
    mgr = tg_auth(_SendCodeDown(), expected_user_id=EXPECTED)
    await mgr.boot()
    with pytest.raises(TgBackendError) as info:
        await mgr.start("+888", owner="s1")
    assert info.value.code == "send_code_failed"
    st = mgr.status()
    assert st.state is TgState.ERROR and st.error == "send_code_failed" and st.attempt_id is None


async def test_start_classified_error_keeps_code() -> None:
    mgr = tg_auth(_BadPhone(), expected_user_id=EXPECTED)
    await mgr.boot()
    with pytest.raises(InvalidPhone):
        await mgr.start("+888", owner="s1")
    assert mgr.status().state is TgState.ERROR and mgr.status().error == "invalid_phone"


async def test_sign_in_backend_failure_keeps_attempt() -> None:
    mgr = tg_auth(_SignInDown(), expected_user_id=EXPECTED)
    await mgr.boot()
    st = await mgr.start("+888", owner="s1")
    with pytest.raises(TgBackendError) as info:
        await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert info.value.code == "sign_in_failed"
    assert mgr.status().state is TgState.AWAITING_CODE


async def test_unbound_accepts_first_account_and_binds_it() -> None:
    bound: list[int] = []

    async def bind(user_id: int) -> int:
        bound.append(user_id)
        return user_id

    backend = FakeTgBackend(user_id=42)
    mgr = tg_auth(backend, expected_user_id=None, bind=bind)
    await mgr.boot()
    st = await mgr.start("+888", owner="s1")
    st = await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.ONLINE and st.user_id == 42 and bound == [42]
    # Привязка держится и без перезапуска: после выхода чужой аккаунт уже не пройдёт.
    await mgr.logout()
    backend.user_id = 7
    st = await mgr.start("+888", owner="s1")
    st = await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.ERROR and st.error == "unexpected_user" and bound == [42]


async def test_unbound_boot_binds_existing_session() -> None:
    bound: list[int] = []

    async def bind(user_id: int) -> int:
        bound.append(user_id)
        return user_id

    mgr = tg_auth(FakeTgBackend(authorized=True), expected_user_id=None, bind=bind)
    assert (await mgr.boot()).state is TgState.ONLINE
    assert bound == [EXPECTED]


async def test_bound_account_is_not_rebound() -> None:
    bound: list[int] = []

    async def bind(user_id: int) -> int:
        bound.append(user_id)
        return user_id

    mgr = tg_auth(FakeTgBackend(authorized=True), expected_user_id=EXPECTED, bind=bind)
    assert (await mgr.boot()).state is TgState.ONLINE
    assert bound == []


async def test_bind_failure_stays_offline_without_logout() -> None:
    # Привязка не записалась (база недоступна): проверить, не занят ли пользователь другим
    # аккаунтом, нельзя — в онлайн не выходим, сессия остаётся для следующей попытки.
    async def bind(user_id: int) -> int:
        raise ConnectionError("db down")

    backend = FakeTgBackend(authorized=True, user_id=42)
    mgr = tg_auth(backend, expected_user_id=None, bind=bind)
    st = await mgr.boot()
    assert st.state is TgState.ERROR and st.error == "bind_failed"
    assert st.bound_user_id is None and not backend.online and not backend.logged_out
    # Клиент отключён: обновления, которые некому разбирать, не копятся.
    assert not backend.connected and backend.authorized


async def test_status_reports_binding_for_life() -> None:
    backend = FakeTgBackend(user_id=42)
    mgr = tg_auth(backend, expected_user_id=None)
    assert mgr.status().bound_user_id is None
    await mgr.boot()
    st = await mgr.start("+888", owner="s1")
    assert st.bound_user_id is None
    st = await mgr.submit_code(st.attempt_id or "", "s1", "12345")
    assert st.state is TgState.ONLINE and st.user_id == 42 and st.bound_user_id == 42
    # Выход из Telegram привязку не снимает.
    st = await mgr.logout()
    assert st.state is TgState.UNAUTHORIZED and st.user_id is None and st.bound_user_id == 42
    assert tg_auth(FakeTgBackend(), expected_user_id=EXPECTED).status().bound_user_id == (EXPECTED)


class _Rig:
    """Вход на транспорте kurigram с фейковым клиентом. Обновление, пришедшее клиенту до
    выхода в онлайн, ждёт в очереди его диспетчера: обработчики стартуют в `go_online`, только
    тогда оно уходит в конвейер (`sink`)."""

    def __init__(
        self,
        *,
        authorized: bool,
        expected_user_id: int | None,
        chats: ChatsSection | None = None,
        bind_error: Exception | None = None,
        stored: int | None = None,
    ) -> None:
        self.sink: list[int] = []
        self.sent = 0
        self.bound: list[int] = []
        self.notes: list[tuple[str, str, str]] = []
        self.settings = Settings(chats=chats or ChatsSection())
        # Сбой записи привязки; привязка, которая уже в базе (None — записывается входящий).
        self.bind_error = bind_error
        self.stored = stored
        self.t = FakeKurigram(authorized=authorized, sink=self._submit, dispatch=True)
        self.tg = tg_auth(
            self.t,
            expected_user_id=expected_user_id,
            bind=self._bind,
            self_chat=lambda user_id: self_chat_fields(self.settings, user_id),
            notifier=self,
        )

    async def _submit(self, msg: IncomingMessage) -> None:
        self.sink.append(msg.msg_id)

    async def _bind(self, user_id: int) -> int:
        if self.bind_error is not None:
            raise self.bind_error
        self.bound.append(user_id)
        return self.stored if self.stored is not None else user_id

    async def notify(self, level: str, code: str, text: str) -> None:
        self.notes.append((level, code, text))

    def update(self) -> None:
        """Обновление от сетевого слоя kurigram текущему клиенту."""
        self.sent += 1
        self.t.client.dispatcher.updates_queue.put_nowait(_game_message(self.sent))

    async def login(self) -> None:
        """Вход по коду; обновление приходит клиенту, пока вход проверяется."""
        assert (await self.tg.boot()).state is TgState.UNAUTHORIZED
        st = await self.tg.start("+888", owner="s1")
        self.update()
        await self.tg.submit_code(st.attempt_id or "", "s1", "12345")

    async def assert_offline(self) -> None:
        await asyncio.sleep(0.02)
        assert self.sink == [] and not self.t.online
        assert all(not c.is_initialized for c in self.t.clients)


async def test_other_user_rejected_before_online() -> None:
    rig = _Rig(authorized=False, expected_user_id=42)
    await rig.login()
    st = rig.tg.status()
    assert st.state is TgState.ERROR and st.error == "unexpected_user" and st.user_id is None
    await rig.assert_offline()
    # Выход из сессии: у Telegram и в хранилище.
    assert "LogOut" in [name for name, _ in rig.t.clients[0].invoked]
    assert rig.t.storage.deleted and rig.bound == [] and st.bound_user_id == 42
    await rig.t.stop()


async def test_user_bound_elsewhere_rejected_before_online() -> None:
    rig = _Rig(authorized=True, expected_user_id=None, bind_error=TgUserTaken(EXPECTED))
    rig.update()
    st = await rig.tg.boot()
    assert st.state is TgState.ERROR and st.error == "tg_user_taken"
    assert st.user_id is None and st.bound_user_id is None
    await rig.assert_offline()
    assert "LogOut" in [name for name, _ in rig.t.clients[0].invoked]
    assert rig.t.storage.deleted
    await rig.t.stop()


async def test_logout_keeps_binding() -> None:
    rig = _Rig(authorized=False, expected_user_id=None)
    await rig.login()
    assert rig.tg.status().state is TgState.ONLINE and rig.bound == [EXPECTED]
    await until(lambda: rig.sink == [1])
    st = await rig.tg.logout()
    assert st.state is TgState.UNAUTHORIZED and st.user_id is None
    assert st.bound_user_id == EXPECTED
    # Повторный вход тем же пользователем — без новой привязки.
    await rig.login()
    assert rig.tg.status().state is TgState.ONLINE and rig.bound == [EXPECTED]
    await rig.t.stop()


async def test_self_chat_on_first_login_binds_but_stays_offline() -> None:
    rig = _Rig(authorized=False, expected_user_id=None, chats=ChatsSection(game_chat_id=EXPECTED))
    await rig.login()
    st = rig.tg.status()
    assert st.state is TgState.ERROR and st.error == "chat_is_self"
    # Привязка записана, сессия не закрыта: после исправления настроек входить заново не нужно.
    assert rig.bound == [EXPECTED] and st.bound_user_id == EXPECTED and st.user_id is None
    await rig.assert_offline()
    # Клиент отключён — обновления, которые некому разбирать, не копятся; сессия — в хранилище.
    assert all(not c.is_connected for c in rig.t.clients)
    assert not rig.t.storage.deleted and await rig.t.storage.user_id() == EXPECTED
    assert "LogOut" not in [name for c in rig.t.clients for name, _ in c.invoked]
    [(level, code, text)] = rig.notes
    assert (level, code) == ("warn", "chat_is_self") and "chats.game_chat_id" in text
    await rig.t.stop()


async def test_self_chat_checked_on_boot() -> None:
    rig = _Rig(
        authorized=True,
        expected_user_id=EXPECTED,
        chats=ChatsSection(game_chat_id=GAME, bulls_invite_chat_id=EXPECTED),
    )
    rig.update()
    st = await rig.tg.boot()
    assert st.state is TgState.ERROR and st.error == "chat_is_self"
    await rig.assert_offline()
    assert [(level, code) for level, code, _ in rig.notes] == [("warn", "chat_is_self")]
    assert "chats.bulls_invite_chat_id" in rig.notes[0][2]
    assert rig.bound == [] and all(not c.is_connected for c in rig.t.clients)
    assert not rig.t.storage.deleted and await rig.t.storage.user_id() == EXPECTED
    assert "LogOut" not in [name for c in rig.t.clients for name, _ in c.invoked]
    # Настройки, которые проверка читает, исправлены — следующий старт подключается заново по той
    # же сессии и выходит в онлайн; обновления доходят до конвейера, а пришедшее отключённому
    # клиенту вернёт сверка истории.
    rig.settings = Settings()
    assert (await rig.tg.boot()).state is TgState.ONLINE
    rig.update()
    await until(lambda: rig.sink == [2])
    await rig.t.stop()


async def test_code_rate_limited() -> None:
    clock = FakeMonotonic(0.0)
    codes = CodeLimiter(10, monotonic=clock)
    backend = _CountingSendCode()
    mgr = tg_auth(backend, codes=codes, account_id=7)
    await mgr.boot()
    for _ in range(3):
        assert (await mgr.start("+888", owner="s1")).state is TgState.AWAITING_CODE
    clock.now = 0.25
    with pytest.raises(CodeRateLimited) as info:
        await mgr.start("+888", owner="s1")
    assert info.value.code == "tg_code_rate_limited" and info.value.retry_after_s == 3599.75
    # Кода не просили, попытка входа и состояние — прежние.
    assert backend.sent == 3 and mgr.status().state is TgState.AWAITING_CODE
    # Лимит — на аккаунт: другой аккаунт того же процесса код получает.
    other = tg_auth(FakeTgBackend(), codes=codes, account_id=8)
    assert (await other.start("+888", owner="s1")).state is TgState.AWAITING_CODE
    clock.now = 3600.0
    assert (await mgr.start("+888", owner="s1")).state is TgState.AWAITING_CODE


class _CountingSendCode(FakeTgBackend):
    def __init__(self) -> None:
        super().__init__()
        self.sent = 0

    async def send_code(self, phone: str) -> str:
        self.sent += 1
        return await super().send_code(phone)


async def test_bind_failure_disconnects_without_logout() -> None:
    rig = _Rig(authorized=True, expected_user_id=None, bind_error=ConnectionError("db down"))
    rig.update()
    st = await rig.tg.boot()
    assert st.state is TgState.ERROR and st.error == "bind_failed"
    await rig.assert_offline()
    assert all(not c.is_connected for c in rig.t.clients)
    assert not rig.t.storage.deleted and await rig.t.storage.user_id() == EXPECTED
    assert "LogOut" not in [name for c in rig.t.clients for name, _ in c.invoked]
    await rig.t.stop()


async def test_binding_in_db_for_other_user_rejected_before_online() -> None:
    # Снимок аккаунта устарел (или запись привязки разошлась с ответом): в базе аккаунт уже
    # привязан к другому пользователю — вход отклоняется, как у привязанного.
    rig = _Rig(authorized=True, expected_user_id=None, stored=42)
    rig.update()
    st = await rig.tg.boot()
    assert st.state is TgState.ERROR and st.error == "unexpected_user"
    assert st.bound_user_id == 42 and st.user_id is None
    await rig.assert_offline()
    assert "LogOut" in [name for name, _ in rig.t.clients[0].invoked]
    assert rig.t.storage.deleted
    await rig.t.stop()


async def test_logout_after_self_chat_closes_telegram_session() -> None:
    # Отказ chat_is_self отключил клиент; выход (кнопка админки, удаление аккаунта) всё равно
    # закрывает сессию у Telegram.
    rig = _Rig(
        authorized=True, expected_user_id=EXPECTED, chats=ChatsSection(game_chat_id=EXPECTED)
    )
    assert (await rig.tg.boot()).error == "chat_is_self"
    st = await rig.tg.logout()
    assert st.state is TgState.UNAUTHORIZED and st.error is None
    assert "LogOut" in [name for c in rig.t.clients for name, _ in c.invoked]
    assert rig.t.storage.deleted and st.bound_user_id == EXPECTED
    await rig.t.stop()
