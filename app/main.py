import asyncio
import logging
import os

import sentry_sdk
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from config import ENABLED_MODULES, ENVIRONMENT, SENTRY_DSN
from core.http_client import close_session
from core.loader import load_modules
from pyrogram import Client
from sentry_sdk.integrations.asyncio import AsyncioIntegration

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s %(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("fenicubot")

# Sentry/GlitchTip - ловит unhandled из хэндлеров (через logging) и фоновых задач (asyncio)
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=ENVIRONMENT,
        integrations=[AsyncioIntegration()],
    )


def _build_proxy() -> dict | None:
    host = os.getenv("PROXY_HOST")
    if not host:
        return None
    proxy = {
        "scheme": os.getenv("PROXY_SCHEME", "socks5"),
        "hostname": host,
        "port": int(os.getenv("PROXY_PORT", "1080")),
    }
    username = os.getenv("PROXY_USER")
    if username:
        proxy["username"] = username
        proxy["password"] = os.getenv("PROXY_PASS", "")
    return proxy


phone = "+0000000000"
proxy = _build_proxy()
if proxy:
    logger.info("Using proxy: %s://%s:%s", proxy["scheme"], proxy["hostname"], proxy["port"])

app = Client(
    "harvestsession",
    api_id=0,
    api_hash="REDACTED_API_HASH",
    phone_number=phone,
    proxy=proxy,
    app_version="1.0",
    system_version="FenicuOS",
    device_model="FenicuGram",
    lang_code="ru",
)


async def shutdown():
    await close_session()


async def main():
    scheduler = AsyncIOScheduler()
    load_modules(app, scheduler, ENABLED_MODULES)
    scheduler.start()
    scheduler.print_jobs()
    await app.start()
    logger.info("Bot started")
    try:
        await asyncio.Event().wait()
    finally:
        await shutdown()
        scheduler.shutdown()
        await app.stop()


if __name__ == "__main__":
    app.run(main())
