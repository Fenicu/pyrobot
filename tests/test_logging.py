import asyncio
import logging

import pytest

from app.__main__ import LOG_FORMAT, pyrogram_log_level
from app.logctx import AccountLogFilter, current_account


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
