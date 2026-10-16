import asyncio
import logging

import pytest

from app.__main__ import LOG_FORMAT, pyrogram_log_level
from app.logctx import AccountLogFilter, NetworkRetryFilter, current_account, quiet_network_retries


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("DEBUG", logging.INFO),
        ("debug", logging.INFO),
        ("INFO", logging.INFO),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
    ],
)
def test_pyrogram_never_below_info(level: str, expected: int) -> None:
    assert pyrogram_log_level(level) == expected


class _Keep(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


async def test_account_filter_marks_records_from_account_tasks() -> None:
    handler = _Keep()
    handler.addFilter(AccountLogFilter())
    logger = logging.getLogger("tests.logctx")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    async def work() -> None:
        await asyncio.sleep(0)
        logger.info("inside")

    try:
        token = current_account.set(7)
        try:
            task = asyncio.create_task(work())
        finally:
            current_account.reset(token)
        logger.info("outside")
        await task
    finally:
        logger.removeHandler(handler)
    got = [(r.getMessage(), getattr(r, "account", None)) for r in handler.records]
    assert got == [("outside", "-"), ("inside", 7)]
    line = logging.Formatter(LOG_FORMAT).format(handler.records[1])
    assert "account=7 inside" in line


def _record(name: str, level: int, msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, msg, args, None)


@pytest.mark.parametrize(
    ("name", "msg", "args"),
    [
        (
            "pyrogram.session.session",
            '[%s] Retrying "%s" due to: %s',
            (1, "messages.Search", "Request timed out"),
        ),
        ("pyrogram.connection.connection", "Unable to connect due to network issues: %s", ("x",)),
        ("pyrogram.connection.connection", "Connection failed! Trying again...", ()),
    ],
)
def test_network_retry_downgraded_to_debug(name: str, msg: str, args: tuple[object, ...]) -> None:
    logger = logging.getLogger(name)
    record = _record(name, logging.WARNING, msg, *args)
    previous = logger.level
    try:
        logger.setLevel(logging.INFO)
        # Уровень pyrogram в приложении — не ниже INFO: повтор после понижения не печатается.
        assert NetworkRetryFilter().filter(record) is False
        assert record.levelno == logging.DEBUG and record.levelname == "DEBUG"
        logger.setLevel(logging.DEBUG)
        assert NetworkRetryFilter().filter(_record(name, logging.WARNING, msg, *args)) is True
    finally:
        logger.setLevel(previous)


@pytest.mark.parametrize(
    ("name", "msg"),
    [
        ("pyrogram.session.session", "Server sent transport error: %s (%s)"),
        (
            "pyrogram.session.session",
            '[%s] Waiting for %s seconds before continuing (required by "%s")',
        ),
        ("pyrogram.client", '[%s] Retrying "%s" due to: %s'),
    ],
)
def test_other_pyrogram_warnings_untouched(name: str, msg: str) -> None:
    record = _record(name, logging.WARNING, msg, 1, 2, 3)
    assert NetworkRetryFilter().filter(record) is True
    assert record.levelno == logging.WARNING


def test_quiet_network_retries_installs_filter(caplog: pytest.LogCaptureFixture) -> None:
    session = logging.getLogger("pyrogram.session.session")
    installed = quiet_network_retries()
    try:
        with caplog.at_level(logging.INFO, logger="pyrogram"):
            session.warning(
                '[%s] Retrying "%s" due to: %s', 1, "updates.GetState", "Request timed out"
            )
            session.warning("Server sent transport error: %s (%s)", 404, "x")
    finally:
        for logger, flt in installed:
            logger.removeFilter(flt)
    assert [r.getMessage() for r in caplog.records] == ["Server sent transport error: 404 (x)"]
