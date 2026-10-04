import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, insert, select

from app.api.app import create_api
from app.config import AppConfig
from app.db.accounts import AccountInfo, AccountRepo, AccountStatus
from app.db.auth_repo import AuthRepo
from app.db.base import Database
from app.db.journal import DbJournal
from app.db.models import Account, MessageRow, SettingsRow
from app.db.notifications import DbNotifier
from app.db.retention import DbRetention
from app.engine.fence import Fence, LeaseLost
from app.engine.host.account import AccountRuntime, RuntimeDeps
from app.engine.host.codes import CodeLimiter
from app.engine.host.host import EngineHost
from app.engine.host.lease import LeaseManager
from app.engine.lag import LoopLagMonitor
from app.main import Runtime
from tests.api.conftest import A1, PASSWORD, login, make_container
from tests.conftest import TEST_DB_URL
from tests.engine.helpers import make_msg, now, until

pytestmark = pytest.mark.db


def _config() -> AppConfig:
    return AppConfig(_env_file=None, database_url=TEST_DB_URL, transport="fake", planner=False)


class Hosts:
    """Хосты теста на одной базе (у каждого — свой менеджер аренды; прочие параметры хоста —
    в `make`). Пауза между стартами пишется в `log` вместе со стартами движков и не ждёт
    (взведённый `gate` — ждёт его); продления нет — местный срок аренды с запасом на весь тест.
    В конце теста хосты останавливаются, соединения блокировок закрываются."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = AccountRepo(db)
        self.hosts: list[EngineHost] = []
        self.leases: list[LeaseManager] = []
        self.log: list[tuple[str, Any]] = []
        self.gate: asyncio.Event | None = None

    async def sleep(self, seconds: float) -> None:
        self.log.append(("sleep", seconds))
        await asyncio.sleep(0)
        if self.gate is not None:
            await self.gate.wait()

    async def make(
        self, holder: str = "host-a", *, reconcile_s: float = 30.0, **kwargs: Any
    ) -> tuple[EngineHost, LeaseManager]:
        deps = RuntimeDeps(
            db=self.db,
            config=_config(),
            accounts=self.repo,
            lag=LoopLagMonitor(),
            codes=CodeLimiter(10),
        )
        leases = LeaseManager(self.db, holder, ttl_s=300.0, busy_retry_s=0.05)
        await leases.open()
        self.leases.append(leases)
        host = EngineHost(
            deps,
            leases,
            max_engines=20,
            start_gap_s=3.0,
            reconcile_s=reconcile_s,
            sleep=self.sleep,
            **kwargs,
        )
        self.hosts.append(host)
        return host, leases

    async def open(
        self, holder: str = "host-a", *, reconcile_s: float = 30.0, **kwargs: Any
    ) -> EngineHost:
        host, _ = await self.make(holder, reconcile_s=reconcile_s, **kwargs)
        await host.start()
        return host

    async def close(self) -> None:
        for host in self.hosts:
            await host.stop()
            await host.supervisor.stop()
        for leases in self.leases:
            await leases.close()


@pytest.fixture
async def hosts(clean_db: Database, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Hosts]:
    made = Hosts(clean_db)
    start = AccountRuntime.start

    async def logged(self: AccountRuntime) -> None:
        made.log.append(("start", self.account_id))
        await start(self)

    monkeypatch.setattr(AccountRuntime, "start", logged)
    try:
        yield made
    finally:
        await made.close()


async def _add(db: Database, status: AccountStatus = "enabled") -> int:
    async with db.sessions() as session, session.begin():
        count = await session.scalar(select(func.count()).select_from(Account))
        account_id = await session.scalar(
            insert(Account).values(name=f"Аккаунт {count}", status=status).returning(Account.id)
        )
    assert account_id is not None
    return account_id


async def _holder(db: Database, account_id: int) -> str | None:
    async with db.sessions() as session:
        return await session.scalar(select(Account.lease_holder).where(Account.id == account_id))


async def _codes(db: Database, account_id: int) -> list[str]:
    return [row.code for row in await DbNotifier(db, account_id).recent()]


async def _wait_status(repo: AccountRepo, account_id: int, status: AccountStatus) -> AccountInfo:
    async with asyncio.timeout(5.0):
        while True:
            account = await repo.get(account_id)
            assert account is not None
            if account.status == status:
                return account
            await asyncio.sleep(0.01)


async def _registered(host: EngineHost, *account_ids: int) -> list[AccountRuntime]:
    """Движки плавного старта (он идёт в фоне), как только они зарегистрированы."""
    runtimes = [await host.wait_registered(account_id, 5.0) for account_id in account_ids]
    assert all(runtime is not None for runtime in runtimes)
    return [runtime for runtime in runtimes if runtime is not None]


def _running(host: EngineHost, account_id: int) -> AccountRuntime:
    runtime = host.get(account_id)
    assert runtime is not None
    return runtime


def _alive(runtime: AccountRuntime) -> bool:
    tasks = runtime.supervisor._tasks
    return bool(tasks) and not any(t.done() for t in tasks.values())


def _spy(runtime: AccountRuntime, calls: list[str]) -> None:
    """Какая остановка досталась движку: `stop` или `abort`."""
    stop, abort = runtime.stop, runtime.abort

    async def stopped() -> None:
        calls.append("stop")
        await stop()

    async def aborted() -> None:
        calls.append("abort")
        await abort()

    runtime.stop = stopped  # type: ignore[method-assign]
    runtime.abort = aborted  # type: ignore[method-assign]


async def test_starts_enabled_accounts_in_id_order_with_gap(
    clean_db: Database, hosts: Hosts
) -> None:
    # Аккаунт без владельца при старте хоста достаётся первой учётке.
    await AuthRepo(clean_db).ensure_owner("admin", "correct horse battery")
    two = await _add(clean_db)
    await _add(clean_db, "disabled")
    await _add(clean_db, "error")
    await _add(clean_db, "deleting")
    host = await hosts.open()
    await _registered(host, 1, two)
    assert hosts.log == [("start", 1), ("sleep", 3.0), ("start", two)]
    assert host.status().engines == [1, two]
    assert _alive(_running(host, 1)) and _alive(_running(host, two))
    account = await hosts.repo.get(1)
    assert account is not None and account.owner_id is not None
    status = host.status()
    assert (status.holder, status.lock_connection_ok, status.busy) == ("host-a", True, {})
    assert status.tasks_ok and isinstance(status.loop_lag_ms, float)
    assert host.capacity == 20


async def test_start_does_not_wait_for_staged_start(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    hosts.gate = asyncio.Event()
    host = await hosts.open()
    # start() вернулся, а плавный старт идёт в фоне: аккаунт 2 ещё ждёт паузы.
    await until(lambda: ("sleep", 3.0) in hosts.log, 5.0)
    assert hosts.log == [("start", 1), ("sleep", 3.0)]
    await _registered(host, 1)
    assert host.get(two) is None and host.status().lock_connection_ok
    # Остановка посреди паузы: старт аккаунта 2 не начинается, захваченная аренда освобождается.
    await host.stop()
    assert hosts.log == [("start", 1), ("sleep", 3.0)] and not host._engines
    assert await _holder(clean_db, 1) is None and await _holder(clean_db, two) is None


async def test_staged_start_continues_after_gap(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    hosts.gate = asyncio.Event()
    host = await hosts.open()
    await until(lambda: ("sleep", 3.0) in hosts.log, 5.0)
    assert host.get(two) is None
    hosts.gate.set()
    await _registered(host, 1, two)
    assert hosts.log == [("start", 1), ("sleep", 3.0), ("start", two)]


async def test_restart_one_account_leaves_other(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    host = await hosts.open()
    first, other = await _registered(host, 1, two)
    await hosts.repo.restart(1)
    host.poke()
    await until(lambda: host.get(1) not in (None, first), 5.0)
    assert _running(host, 1).generation == first.generation + 1
    assert not first.supervisor._tasks
    assert host.get(two) is other and _alive(other)
    # Перезапуск штатный: аренда освобождена и захвачена снова.
    assert _running(host, 1).fence.epoch == first.fence.epoch + 1


async def test_missed_poke_caught_by_periodic_reconcile(clean_db: Database, hosts: Hosts) -> None:
    host = await hosts.open(reconcile_s=0.1)
    (first,) = await _registered(host, 1)
    await hosts.repo.restart(1)
    await until(lambda: host.get(1) not in (None, first), 5.0)
    assert _running(host, 1).generation == first.generation + 1


async def test_retry_due_during_pass_is_not_postponed(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    two = await _add(clean_db)
    # Аккаунт 1 занят другим хостом, а старт аккаунта 2 держится: срок повтора захвата аккаунта 1
    # наступает посреди прохода сверки, и к его концу уже прошёл.
    other = LeaseManager(clean_db, "other-host", ttl_s=300.0)
    await other.open()
    try:
        fence = await other.acquire(1)
        assert isinstance(fence, Fence)
        gate = asyncio.Event()
        start = AccountRuntime._start

        async def held(self: AccountRuntime) -> None:
            if self.account_id == two:
                await gate.wait()
            await start(self)

        monkeypatch.setattr(AccountRuntime, "_start", held)
        host = await hosts.open()
        await until(lambda: host.host_reason(1) == "locked_elsewhere", 5.0)
        loop = asyncio.get_running_loop()
        await until(lambda: loop.time() > host._retry_at[1], 5.0)
        await other.release(fence)
        gate.set()
        # Просроченный повтор исполняется сразу, а не через `reconcile_s` (30 с).
        await until(lambda: host.get(1) is not None and host.get(two) is not None, 5.0)
    finally:
        await other.close()


async def test_disable_stops_enable_starts(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    host = await hosts.open()
    first, _ = await _registered(host, 1, two)
    calls: list[str] = []
    _spy(first, calls)
    await hosts.repo.update(1, enabled=False, capacity=20)
    host.poke()
    await until(lambda: host.get(1) is None and not first.supervisor._tasks, 5.0)
    assert calls == ["stop"]
    await until(lambda: 1 not in host._engines, 5.0)
    assert await _holder(clean_db, 1) is None
    await hosts.repo.update(1, enabled=True, capacity=20)
    host.poke()
    await until(lambda: host.get(1) is not None, 5.0)
    assert _alive(_running(host, 1))
    # Удаляемый аккаунт тоже останавливается, и сверка его не поднимает.
    other = _running(host, two)
    await hosts.repo.mark_deleting(two)
    host.poke()
    await until(lambda: two not in host._engines, 5.0)
    assert not other.supervisor._tasks and host.get(two) is None
    host.poke()
    await asyncio.sleep(0.1)
    assert host.get(two) is None and host.status().engines == [1]


async def test_crash_loop_sets_error_and_notifies(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    host = await hosts.open()
    engine, other = await _registered(host, 1, two)
    calls: list[str] = []
    _spy(engine, calls)
    runs: list[int] = []

    async def boom() -> None:
        runs.append(1)
        raise RuntimeError("boom")

    # Задача движка 1 падает пять раз подряд (без пауз): супервизор движка сообщает хосту.
    engine.supervisor._base = engine.supervisor._max = 0.001
    engine.supervisor.start("boom", boom)
    await until(lambda: 1 not in host._engines, 5.0)
    assert len(runs) == 5 and host.get(1) is None
    # Аренда действовала — штатная остановка с доработкой конвейера.
    assert calls == ["stop"] and not engine.supervisor._tasks
    account = await hosts.repo.get(1)
    assert account is not None
    assert (account.status, account.status_reason) == ("error", "crash_loop:boom")
    rows = [r for r in await DbNotifier(clean_db, 1).recent() if r.code == "account_crash_loop"]
    assert len(rows) == 1 and rows[0].level == "error"
    assert await _holder(clean_db, 1) is None
    assert host.get(two) is other and _alive(other)
    host.poke()
    await asyncio.sleep(0.1)
    assert host.get(1) is None and host.host_reason(1) is None


async def test_error_status_notifies_server(clean_db: Database, hosts: Hosts) -> None:
    from app.db.notifications import ServerNotifier

    two = await _add(clean_db)
    host = await hosts.open()
    (_one, _other) = await _registered(host, 1, two)

    engine = _running(host, 1)

    async def boom() -> None:
        raise RuntimeError("boom")

    engine.supervisor._base = engine.supervisor._max = 0.001
    engine.supervisor.start("boom", boom)
    await until(lambda: 1 not in host._engines, 5.0)

    server = ServerNotifier(clean_db)
    server_rows = await server.recent()
    err_rows = [r for r in server_rows if r.code == "account_error"]
    assert len(err_rows) == 1
    assert err_rows[0].level == "error"
    assert "account 1 -> error: crash_loop:boom" in err_rows[0].text
    assert err_rows[0].account_id is None


async def test_crash_loop_during_start_is_acted_on(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = AccountRuntime._start

    async def crashing(self: AccountRuntime) -> None:
        await start(self)
        # Серия сбоев задачи, пока старт ещё идёт.
        await self.supervisor._on_crash_loop("planner")  # type: ignore[misc]

    monkeypatch.setattr(AccountRuntime, "_start", crashing)
    host, _ = await hosts.make()
    registered = asyncio.create_task(host.wait_registered(1, 0.3))
    await host.start()
    account = await _wait_status(hosts.repo, 1, "error")
    assert account.status_reason == "crash_loop:planner"
    await until(lambda: 1 not in host._engines, 5.0)
    # Движок с серией сбоев так и не регистрировался.
    assert await registered is None
    assert "account_crash_loop" in await _codes(clean_db, 1)
    assert await _holder(clean_db, 1) is None


async def test_start_failure_sets_error_other_accounts_unaffected(
    clean_db: Database, hosts: Hosts
) -> None:
    await AuthRepo(clean_db).ensure_owner("admin", PASSWORD)
    async with clean_db.sessions() as session, session.begin():
        session.add(SettingsRow(account_id=1, version=1, data={"engine": {"mode": "warp"}}))
    two = await _add(clean_db)
    host = await hosts.open()
    account = await _wait_status(hosts.repo, 1, "error")
    assert account.status_reason == "start_failed:ValidationError"
    await _registered(host, two)
    assert host.get(1) is None and 1 not in host._engines and host.host_reason(1) is None
    assert await _holder(clean_db, 1) is None
    assert _alive(_running(host, two))
    # Без движка настройки читаются и правятся через API (раздел 4.4 спеки); аккаунт включён —
    # движок поднимается без перезапуска хоста.
    c = replace(make_container(clean_db), engines=host)
    async with AsyncClient(transport=ASGITransport(create_api(c)), base_url="http://t") as client:
        headers = {"X-CSRF-Token": await login(client)}
        settings = (await client.get(f"{A1}/settings")).json()
        assert settings["values"]["engine"]["mode"] == "warp"
        fix = {"version": 1, "changes": {"engine": {"mode": "dry_run"}}}
        r = await client.patch(f"{A1}/settings", headers=headers, json=fix)
        assert (r.status_code, r.json()["version"]) == (200, 2)
        r = await client.patch(A1, headers=headers, json={"enabled": True})
        assert (r.status_code, r.json()["status"]) == (200, "enabled")
        await until(lambda: host.get(1) is not None, 5.0)
    assert _alive(_running(host, 1)) and _alive(_running(host, two))


async def test_two_hosts_one_engine_per_account(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    a, _ = await hosts.make("host-a")
    b, _ = await hosts.make("host-b")
    await asyncio.gather(a.start(), b.start())

    def settled(account_id: int) -> bool:
        return any(
            host.get(account_id) is not None and other.host_reason(account_id) is not None
            for host, other in ((a, b), (b, a))
        )

    await until(lambda: settled(1) and settled(two), 5.0)
    for account_id in (1, two):
        owners = [host for host in (a, b) if host.get(account_id) is not None]
        assert len(owners) == 1
        other = b if owners[0] is a else a
        assert other.host_reason(account_id) == "locked_elsewhere"
        assert other.status().busy[account_id] == "locked_elsewhere"
        assert await _holder(clean_db, account_id) == owners[0].status().holder
    # Хост A остановился — B берёт его аккаунты по повтору захвата.
    await a.stop()
    await until(lambda: b.get(1) is not None and b.get(two) is not None, 5.0)
    assert b.host_reason(1) is None and b.status().busy == {}


async def test_lost_lease_aborts_and_reacquires(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    host = await hosts.open()
    first, other = await _registered(host, 1, two)
    calls: list[str] = []
    _spy(first, calls)
    first.fence.revoke()
    # Останавливаемый движок API уже не видит.
    assert host.get(1) is None
    await until(lambda: host.get(1) not in (None, first), 5.0)
    assert calls == ["abort"] and not first.supervisor._tasks
    assert _running(host, 1).fence.epoch == first.fence.epoch + 1
    assert (await _codes(clean_db, 1)).count("lock_lost") == 1
    assert host.get(two) is other and _alive(other)
    account = await hosts.repo.get(1)
    assert account is not None and account.status == "enabled"


async def test_lease_lost_during_start_is_lease_loss(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = AccountRuntime._start
    tries: list[int] = []

    async def losing(self: AccountRuntime) -> None:
        tries.append(self.fence.epoch)
        if len(tries) == 1:
            self.fence.revoke()
            raise LeaseLost("lost during start")
        await start(self)

    monkeypatch.setattr(AccountRuntime, "_start", losing)
    host = await hosts.open()
    await until(lambda: host.get(1) is not None, 5.0)
    # Не сбой старта: статус не тронут, аренда захвачена заново на следующей сверке.
    account = await hosts.repo.get(1)
    assert account is not None and (account.status, account.status_reason) == ("enabled", None)
    assert tries == [1, 2] and _running(host, 1).fence.epoch == 2


async def test_restart_waits_for_previous_runtime(clean_db: Database, hosts: Hosts) -> None:
    host = await hosts.open()
    (first,) = await _registered(host, 1)
    release = asyncio.Event()
    stop = first.stop
    stopping: list[bool] = []

    async def held() -> None:
        stopping.append(True)
        await release.wait()
        await stop()

    first.stop = held  # type: ignore[method-assign]
    hosts.log.clear()
    await hosts.repo.restart(1)
    host.poke()
    await until(lambda: stopping == [True], 5.0)
    host.poke()
    await asyncio.sleep(0.1)
    # Прежний движок ещё останавливается: новый не стартует, аренда у прежнего.
    assert ("start", 1) not in hosts.log and host.get(1) is None
    release.set()
    await until(lambda: host.get(1) is not None, 5.0)
    assert hosts.log.count(("start", 1)) == 1
    assert _running(host, 1).generation == first.generation + 1


async def test_wait_registered_waits_for_live_engine(clean_db: Database, hosts: Hosts) -> None:
    other = LeaseManager(clean_db, "other-host")
    await other.open()
    try:
        fence = await other.acquire(1)
        assert isinstance(fence, Fence)
        host = await hosts.open()
        await until(lambda: host.host_reason(1) == "locked_elsewhere", 5.0)
        assert host.get(1) is None
        assert await host.wait_registered(1, 0.05) is None
        registered = asyncio.create_task(host.wait_registered(1, 5.0))
        await other.release(fence)
        engine = await registered
        assert engine is not None and engine is host.get(1)
        assert host.host_reason(1) is None
        # Движок с потерянной арендой не зарегистрирован: ждём новый, а не останавливаемый.
        engine.fence.revoke()
        again = await host.wait_registered(1, 5.0)
        assert again is not None and again is not engine and again is host.get(1)
    finally:
        await other.close()


async def test_host_reason_never_stale(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = LeaseManager(clean_db, "other-host")
    await other.open()
    try:
        two = await _add(clean_db)
        fence = await other.acquire(1)
        fence2 = await other.acquire(two)
        assert isinstance(fence, Fence) and isinstance(fence2, Fence)
        host = await hosts.open()
        await until(lambda: host.host_reason(two) is not None, 5.0)
        assert (host.host_reason(1), host.host_reason(two)) == ("locked_elsewhere",) * 2
        # Аккаунт выключен — причины захвата у него больше нет.
        await hosts.repo.update(1, enabled=False, capacity=20)
        host.poke()
        await until(lambda: host.host_reason(1) is None, 5.0)
        assert host.status().busy == {two: "locked_elsewhere"}

        # Старт не удался — причины тоже нет, есть статус error.
        async def failing(self: AccountRuntime) -> None:
            raise RuntimeError("broken")

        monkeypatch.setattr(AccountRuntime, "_start", failing)
        await other.release(fence2)
        account = await _wait_status(hosts.repo, two, "error")
        assert account.status_reason == "start_failed:RuntimeError"
        assert host.host_reason(two) is None and host.status().busy == {}
        await other.release(fence)
    finally:
        await other.close()


async def test_lease_acquires_one_at_a_time(
    clean_db: Database, hosts: Hosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = [await _add(clean_db, "disabled") for _ in range(3)]
    host, leases = await hosts.make()
    acquire = leases.acquire
    inflight: list[int] = []
    most = [0]

    async def counted(account_id: int) -> Any:
        inflight.append(account_id)
        most[0] = max(most[0], len(inflight))
        try:
            await asyncio.sleep(0.01)
            return await acquire(account_id)
        finally:
            inflight.remove(account_id)

    monkeypatch.setattr(leases, "acquire", counted)
    await host.start()
    for account_id in ids:
        await hosts.repo.update(account_id, enabled=True, capacity=20)
        host.poke()
    await until(lambda: all(host.get(i) is not None for i in [1, *ids]), 5.0)
    _running(host, 1).fence.revoke()
    await until(lambda: host.get(1) is not None, 5.0)
    assert most[0] == 1


async def test_stop_stops_live_aborts_lost_and_releases(clean_db: Database, hosts: Hosts) -> None:
    two = await _add(clean_db)
    host = await hosts.open()
    live, lost = await _registered(host, 1, two)
    live_calls: list[str] = []
    lost_calls: list[str] = []
    _spy(live, live_calls)
    _spy(lost, lost_calls)
    # Срок аренды наступил, а аварийная остановка ещё не прошла.
    lost.fence.on_lost = None
    lost.fence.revoke()
    await host.stop()
    assert (live_calls, lost_calls) == (["stop"], ["abort"])
    assert host.get(1) is None and host.get(two) is None and not host._engines
    # Действующая аренда освобождена, потерянная — нет (после срока хост в базу не пишет).
    assert await _holder(clean_db, 1) is None
    assert await _holder(clean_db, two) == "host-a"


async def test_process_tasks_restart_without_crash_limit(hosts: Hosts) -> None:
    host = await hosts.open()
    runs: list[int] = []

    async def flaky() -> None:
        runs.append(1)
        raise RuntimeError("boom")

    host.supervisor._base = host.supervisor._max = 0.001
    host.supervisor.start("flaky", flaky)
    # Задача процесса не бросается после серии сбоев, а перезапускается дальше.
    await until(lambda: len(runs) >= 8, 5.0)
    assert not host.supervisor._tasks["flaky"].done()
    await host.supervisor.cancel("flaky")


async def _journal(db: Database, account_id: int, *days: int) -> None:
    journal = DbJournal(db, account_id)
    for i, age in enumerate(days):
        msg = make_msg(f"m{i}", msg_id=account_id * 100 + i, received_at=now() - timedelta(age))
        await journal.append(msg, [], None, 0)


async def _messages(db: Database, account_id: int) -> int:
    async with db.sessions() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(MessageRow)
                .where(MessageRow.account_id == account_id)
            )
            or 0
        )


async def test_retention_covers_disabled_skips_deleting(clean_db: Database) -> None:
    disabled = await _add(clean_db, "disabled")
    failed = await _add(clean_db, "error")
    deleting = await _add(clean_db, "deleting")
    for account_id in (1, disabled, failed, deleting):
        await _journal(clean_db, account_id, 100, 10)
    runtime = Runtime(_config())
    try:
        await runtime._retention_pass()
    finally:
        await runtime.db.dispose()
    # Все активные/выключенные/упавшие аккаунты чистятся по политике сервера (90 дней),
    # удаляемые пропускаются.
    assert await _messages(clean_db, 1) == 1
    assert await _messages(clean_db, failed) == 1
    assert await _messages(clean_db, disabled) == 1
    assert await _messages(clean_db, deleting) == 2


async def test_retention_failure_notifies_account_once(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    two = await _add(clean_db)
    purge = DbRetention.purge

    async def broken(self: DbRetention, *args: Any) -> dict[str, int]:
        if self._account_id == 1:
            raise ConnectionError("db down")
        return await purge(self, *args)

    monkeypatch.setattr(DbRetention, "purge", broken)
    await _journal(clean_db, two, 100)
    runtime = Runtime(_config())
    try:
        for _ in range(3):
            await runtime._retention_pass()
    finally:
        await runtime.db.dispose()
    # Сбой одного аккаунта не мешает остальным; уведомление — одно на серию.
    assert (await _codes(clean_db, 1)).count("retention_failed") == 1
    assert "retention_failed" not in await _codes(clean_db, two)
    assert await _messages(clean_db, two) == 0


async def test_stats_counts_restarts_and_last_error(clean_db: Database, hosts: Hosts) -> None:
    host = await hosts.open()
    (first,) = await _registered(host, 1)

    # 1. Начальный старт движка: running=True, tg_online=False, restarts_24h=0
    # (первый старт исключен)
    st = host.stats(1)
    assert st.running is True
    assert st.tg_online is False
    assert st.restarts_24h == 0
    assert st.last_error_code is None
    assert st.last_error_at is None

    # 2. Перезапуск движка: restarts_24h становится 1
    await hosts.repo.restart(1)
    host.poke()
    await until(lambda: host.get(1) not in (None, first), 5.0)
    st = host.stats(1)
    assert st.running is True
    assert st.restarts_24h == 1

    # 3. Фатальная ошибка (потеря аренды lock_lost)
    engine = _running(host, 1)
    engine.fence.revoke()
    await until(lambda: host.stats(1).last_error_code == "lock_lost", 5.0)
    st_err = host.stats(1)
    assert st_err.last_error_code == "lock_lost"
    assert st_err.last_error_at is not None
