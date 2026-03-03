import logging

import aiohttp

logger = logging.getLogger(__name__)

HEADERS = {"Content-Type": "application/json", "user-agent": "maledict_client"}

_session: aiohttp.ClientSession | None = None


def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=10)
        _session = aiohttp.ClientSession(headers=HEADERS, timeout=timeout)
    return _session


async def close_session() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None
        logger.info("HTTP session closed")
