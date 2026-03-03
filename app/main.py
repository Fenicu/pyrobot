import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from config import ENABLED_MODULES
from core.http_client import close_session
from core.loader import load_modules
from pyrogram import Client

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s %(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("fenicubot")

phone = "+0000000000"
app = Client(
    "harvestsession",
    api_id=0,
    api_hash="REDACTED_API_HASH",
    phone_number=phone,
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
