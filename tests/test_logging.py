import logging

import pytest

from app.__main__ import pyrogram_log_level


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
