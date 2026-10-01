"""Замер движков в одном процессе (раздел 4.8 спеки): сколько стоят N аккаунтов на одном хосте.

    uv run python tools/bench_engines.py --engines 5,10,20,40 --minutes 5

Для каждого N создаётся база `pyrobot_bench` (на сервере из `--database-url`; база из самого URL
не трогается), накатываются миграции, заводятся N включённых аккаунтов с состоянием с прода
(`tests/fixtures/api/state.json`) и в отдельном процессе стартует `Runtime` с транспортом fake:
хост движков, аренды и задачи процесса те же, что на сервере. У каждого движка свой фейковый
бэкенд входа — аккаунт онлайн, шлюз, реакции и сверка работают. Планировщик выключен
(`planner=False`): игра фейковая и на команды не отвечает, сценарии ходили бы по кругу по
тайм-аутам — это нагрузка не прода. В конвейер каждого аккаунта воспроизводятся записанные
сообщения игры (`tests/fixtures/game`) — как приходили бы из Telegram:

- фон: сообщения случайных семейств, пуассоновский поток `--rate` в минуту на аккаунт;
- всплески: каждые `--burst-every` с все аккаунты разом получают `--burst-size` правок экрана
  метро подряд (с шагом `BURST_GAP_S`) — как игровое событие, которое приходит всем в один
  момент.

Замер идёт `--minutes` после выхода всех движков в онлайн и прогрева. База после замера
удаляется. Печатается таблица: N, RSS МБ, CPU %, задержка цикла p50, p99 и максимум, мс. Не в CI.
Только Linux (RSS — из /proc).

CPU считается процессом приложения: 100 % — одно полное ядро (цикл событий один), Postgres не
входит. Задержка цикла — перерасход сна в 50 мс на цикле событий процесса, как у
`LoopLagMonitor`, но чаще: монитор процесса просыпается раз в 0,5 с и ловит редкие паузы хуже."""

import argparse
import asyncio
import json
import logging
import math
import random
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import func, insert, select, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.config import AppConfig, DbConfig  # noqa: E402
from app.db.models import Account, MessageRow, StateSnapshot  # noqa: E402
from app.engine.host.account import AccountRuntime  # noqa: E402
from app.engine.pipeline import Pipeline  # noqa: E402
from app.engine.tg_auth import TgAuthBackend  # noqa: E402
from app.engine.transport.base import Transport  # noqa: E402
from app.engine.transport.fake import FakeTgBackend, FakeTransport  # noqa: E402
from app.engine.types import IncomingMessage  # noqa: E402
from app.main import Runtime  # noqa: E402
from tests.fixtures import game, game_versions  # noqa: E402

P99_LIMIT_MS = 100.0
LAG_INTERVAL_S = 0.05
# Пауза между стартами движков в замере: плавный старт (3 с на аккаунт на сервере) в замер не
# входит, измеряется установившийся режим.
START_GAP_S = 0.05
# Шаг правок метро внутри всплеска.
BURST_GAP_S = 0.2
# Пользователь Telegram фейкового бэкенда аккаунта — свой у каждого (привязка уникальна).
TG_USER_BASE = 1_000_000
# Первый msg_id фоновых сообщений аккаунта: выше любого id в фикстурах, дальше — по порядку.
FIRST_ID = 10_000_000
STATE = ROOT / "tests" / "fixtures" / "api" / "state.json"
GAME_DIR = ROOT / "tests" / "fixtures" / "game"
BENCH_DB = "pyrobot_bench"
METRO = "metro"


@dataclass
class Traffic:
    """Сообщений доставлено в конвейеры: фон и всплески."""

    sent: int = 0


@dataclass
class Feed:
    """Записанные сообщения игры для одного аккаунта: свои id и ревизии, время — «сейчас», как
    у только что пришедших."""

    rng: random.Random
    pool: list[IncomingMessage]
    metro: list[IncomingMessage]
    order: list[IncomingMessage] = field(default_factory=list)
    next_id: int = FIRST_ID
    edits: int = 0
    revision: int = 0

    def background(self) -> IncomingMessage:
        """Следующее сообщение набора — в случайном порядке, а набор кончился — по новому кругу."""
        if not self.order:
            self.order = self.rng.sample(self.pool, len(self.pool))
        self.next_id += 1
        self.revision += 1
        return _fresh(self.order.pop(), self.next_id, self.revision)

    def burst(self) -> IncomingMessage:
        msg = self.metro[self.edits % len(self.metro)]
        self.edits += 1
        self.revision += 1
        return _fresh(msg, msg.msg_id, self.revision)


def _fresh(msg: IncomingMessage, msg_id: int, revision: int) -> IncomingMessage:
    """Сообщение фикстуры как только что пришедшее: свои id и ревизия, время — «сейчас»."""
    moment = datetime.now(UTC)
    return replace(
        msg,
        msg_id=msg_id,
        revision=revision if msg.kind == "edit" else 0,
        date=moment,
        received_at=moment,
        created_at=moment,
    )


def _pool() -> list[IncomingMessage]:
    """Все записанные сообщения игры, кроме метро (его правки — во всплесках): последняя версия
    каждого."""
    families = sorted(path.stem for path in GAME_DIR.glob("*.jsonl"))
    return [m for family in families if family != METRO for m in game(family).values()]


def _feeds(accounts: list[int], pool: list[IncomingMessage], seed: int) -> dict[int, Feed]:
    # Самый длинный забег метро: правки одного сообщения подряд.
    metro = max((game_versions(METRO, msg_id) for msg_id in game(METRO)), key=len)
    return {a: Feed(random.Random(seed * 1000 + a), pool, metro) for a in accounts}


async def _background(feed: Feed, pipeline: Pipeline, rate_per_min: float, t: Traffic) -> None:
    while True:
        await asyncio.sleep(feed.rng.expovariate(rate_per_min / 60.0))
        await pipeline.submit(feed.background())
        t.sent += 1


async def _burst_one(feed: Feed, pipeline: Pipeline, size: int, t: Traffic) -> None:
    for _ in range(size):
        await pipeline.submit(feed.burst())
        t.sent += 1
        await asyncio.sleep(BURST_GAP_S)


async def _bursts(
    feeds: list[tuple[Feed, Pipeline]], every_s: float, size: int, t: Traffic
) -> None:
    await asyncio.sleep(every_s / 2)
    while True:
        started = time.monotonic()
        await asyncio.gather(*(_burst_one(feed, pipe, size, t) for feed, pipe in feeds))
        await asyncio.sleep(max(0.0, every_s - (time.monotonic() - started)))


async def _lag_samples(samples: list[tuple[float, float]]) -> None:
    """Перерасход сна, мс, с меткой времени: пауза цикла событий растягивает сон на себя."""
    loop = asyncio.get_running_loop()
    while True:
        start = loop.time()
        await asyncio.sleep(LAG_INTERVAL_S)
        now = loop.time()
        samples.append((now, max(0.0, (now - start - LAG_INTERVAL_S) * 1000)))


def _percentile(ordered: list[float], q: float) -> float:
    """Ближайший ранг: p99 из 3600 замеров — тридцать седьмой с конца."""
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def _rss_mb() -> float:
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    raise RuntimeError("VmRSS not found in /proc/self/status")


async def _online_transport(
    self: AccountRuntime, pipeline: Pipeline
) -> tuple[Transport, TgAuthBackend]:
    """Замена `AccountRuntime._make_transport`: фейковый транспорт, но вход уже выполнен —
    аккаунт онлайн, шлюз и реакции готовы к работе; пользователь Telegram — свой у каждого."""
    return FakeTransport(), FakeTgBackend(authorized=True, user_id=TG_USER_BASE + self.account_id)


async def _seed(runtime: Runtime, count: int) -> list[int]:
    """`count` включённых аккаунтов с состоянием с прода."""
    snapshot = json.loads(STATE.read_text(encoding="utf-8"))
    async with runtime.db.sessions() as session, session.begin():
        have = await session.scalar(select(func.count()).select_from(Account)) or 0
        for number in range(have, count):
            await session.execute(insert(Account).values(name=f"Замер {number + 1}"))
        ids = list(await session.scalars(select(Account.id).order_by(Account.id).limit(count)))
        for account_id in ids:
            await session.execute(
                insert(StateSnapshot).values(
                    account_id=account_id, version=snapshot["version"], state=snapshot["state"]
                )
            )
    return ids


async def _engines(runtime: Runtime, accounts: list[int]) -> list[AccountRuntime]:
    """Движки всех аккаунтов, как только плавный старт их зарегистрирует."""
    found = await asyncio.gather(
        *(runtime.host.wait_registered(a, 60 + 5 * len(accounts)) for a in accounts)
    )
    return [engine for engine in found if engine is not None]


async def _measure(args: argparse.Namespace) -> dict[str, Any]:
    n = args.engines
    config = AppConfig(
        _env_file=None,
        database_url=args.database_url,
        transport="fake",
        planner=False,
        max_engines=n,
        engine_start_gap_s=START_GAP_S,
    )
    traffic = Traffic()
    AccountRuntime._make_transport = _online_transport
    runtime = Runtime(config)
    # Ретеншн (первый проход через 5 минут) в замер разной длины не входит.
    runtime.retention_first_s = 1e9
    accounts = await _seed(runtime, n)
    samples: list[tuple[float, float]] = []
    lag = asyncio.create_task(_lag_samples(samples))
    tasks: list[asyncio.Task[None]] = []
    try:
        await runtime.start()
        engines = await _engines(runtime, accounts)
        rss_started = _rss_mb()
        await asyncio.sleep(args.warmup)
        feeds = _feeds(accounts, _pool(), args.seed)
        pairs = [(feeds[e.account_id], e.pipeline) for e in engines if e.pipeline is not None]
        for feed, pipe in pairs:
            tasks.append(asyncio.create_task(_background(feed, pipe, args.rate, traffic)))
        tasks.append(
            asyncio.create_task(_bursts(pairs, args.burst_every, args.burst_size, traffic))
        )
        loop = asyncio.get_running_loop()
        began, cpu_began, measured_from = time.monotonic(), time.process_time(), loop.time()
        await asyncio.sleep(args.minutes * 60)
        measured_to = loop.time()
        wall, cpu = time.monotonic() - began, time.process_time() - cpu_began
        rss = _rss_mb()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tasks.clear()
        lags = sorted(v for at, v in samples if measured_from <= at <= measured_to)
        drained = all(await asyncio.gather(*(p.drain(30) for _, p in pairs)))
        async with runtime.db.sessions() as session:
            stored = await session.scalar(select(func.count()).select_from(MessageRow))
        status = runtime.host.status()
        return {
            "engines": n,
            "alive": len(status.engines),
            "tasks_ok": status.tasks_ok and all(e.supervisor.healthy() for e in engines),
            "rss_mb": rss,
            "rss_started_mb": rss_started,
            "cpu_pct": cpu / wall * 100,
            "p50_ms": _percentile(lags, 0.5),
            "p99_ms": _percentile(lags, 0.99),
            "max_ms": lags[-1],
            "lag_samples": len(lags),
            "sent": traffic.sent,
            "stored": stored,
            "drained": drained,
        }
    finally:
        for task in tasks:
            task.cancel()
        lag.cancel()
        await runtime.stop()


def _worker(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    print(json.dumps(asyncio.run(_measure(args))))


async def _admin(url: str, *statements: str) -> None:
    """Команды сервера (создание и удаление базы) — через служебную базу `postgres`."""
    engine = create_async_engine(
        make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as conn:
            for statement in statements:
                await conn.execute(text(statement))
    finally:
        await engine.dispose()


def _migrate(url: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "app" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")


def _run_one(args: argparse.Namespace, n: int) -> dict[str, Any]:
    server = make_url(args.database_url)
    url = server.set(database=args.bench_db).render_as_string(hide_password=False)
    asyncio.run(
        _admin(args.database_url, f'DROP DATABASE IF EXISTS "{args.bench_db}" WITH (FORCE)')
    )
    asyncio.run(_admin(args.database_url, f'CREATE DATABASE "{args.bench_db}"'))
    try:
        _migrate(url)
        argv = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--engines",
            str(n),
            "--minutes",
            str(args.minutes),
            "--rate",
            str(args.rate),
            "--burst-every",
            str(args.burst_every),
            "--burst-size",
            str(args.burst_size),
            "--warmup",
            str(args.warmup),
            "--seed",
            str(args.seed),
            "--database-url",
            url,
        ]
        done = subprocess.run(argv, stdout=subprocess.PIPE, text=True, check=True)
        return dict(json.loads(done.stdout.strip().splitlines()[-1]))
    finally:
        asyncio.run(
            _admin(args.database_url, f'DROP DATABASE IF EXISTS "{args.bench_db}" WITH (FORCE)')
        )


def _table(results: list[dict[str, Any]]) -> str:
    head = "| N | RSS, МБ | CPU, % | цикл p50, мс | цикл p99, мс | цикл max, мс | сообщений |"
    rows = [head, "|--:|--:|--:|--:|--:|--:|--:|"]
    for r in results:
        rows.append(
            f"| {r['engines']} | {r['rss_mb']:.0f} | {r['cpu_pct']:.1f} | {r['p50_ms']:.1f} "
            f"| {r['p99_ms']:.1f} | {r['max_ms']:.1f} | {r['sent']} |"
        )
    return "\n".join(rows)


def _problems(r: dict[str, Any]) -> list[str]:
    """Прогон считается чистым, только если все движки дожили, а сообщения дошли до журнала."""
    found = []
    if r["alive"] != r["engines"]:
        found.append(f"движков в конце {r['alive']} из {r['engines']}")
    if not r["tasks_ok"]:
        found.append("задача упала и перезапускается")
    if not r["drained"]:
        found.append("очереди конвейеров не опустели за 30 с")
    if r["stored"] != r["sent"]:
        found.append(f"в журнале {r['stored']} сообщений из {r['sent']}")
    return found


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--engines", default="5,10,20,40", help="N через запятую")
    parser.add_argument("--minutes", type=float, default=5.0, help="длина замера на каждое N")
    parser.add_argument("--rate", type=float, default=4.0, help="сообщений в минуту на аккаунт")
    parser.add_argument("--burst-every", type=float, default=60.0, help="период всплесков, с")
    parser.add_argument("--burst-size", type=int, default=8, help="правок метро на аккаунт")
    parser.add_argument("--warmup", type=float, default=10.0, help="прогрев перед замером, с")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--database-url", default=DbConfig().database_url)
    parser.add_argument("--bench-db", default=BENCH_DB)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.worker:
        args.engines = int(args.engines)
        _worker(args)
        return 0
    if "bench" not in args.bench_db:
        print("--bench-db: имя базы замера должно содержать «bench»", file=sys.stderr)
        return 2
    results = []
    for n in (int(part) for part in args.engines.split(",")):
        print(f"N={n}: {args.minutes} мин...", file=sys.stderr, flush=True)
        try:
            results.append(_run_one(args, n))
        except subprocess.CalledProcessError as exc:
            print(
                f"N={n}: замер упал (код {exc.returncode}), вывод процесса выше", file=sys.stderr
            )
            return 1
        for problem in _problems(results[-1]):
            print(f"N={n}: {problem}", file=sys.stderr)
    print(_table(results))
    fit = [r["engines"] for r in results if r["p99_ms"] < P99_LIMIT_MS and not _problems(r)]
    verdict = f"наибольшее N с p99 < {P99_LIMIT_MS:.0f} мс: {max(fit)}" if fit else "ни одно N"
    print(f"\n{verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
