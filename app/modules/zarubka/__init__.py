import asyncio
import logging
import re

from config import ZARUB
from core.module import BaseModule
from pyrogram import Client, filters, types
from pyrogram.handlers import MessageHandler

logger = logging.getLogger(__name__)

PROMO_FINDER = re.compile(r"/promo_(\w+)")


class ZarubkaModule(BaseModule):
    name = "zarubka"

    async def apply_promo(self, client: Client, message: types.Message):
        if not message.text:
            logger.warning("Message has no text")
            return

        match = PROMO_FINDER.search(message.text)
        if not match:
            logger.warning("Promo code not found in message")
            return

        promo_code = match.group(1)
        await asyncio.sleep(3)
        logger.info("Applying promo code: %s", promo_code)
        await message.reply_text(f"/promo_{promo_code}")

    def register_handlers(self) -> None:
        self.app.add_handler(
            MessageHandler(
                self.apply_promo,
                filters.user(ZARUB) & filters.regex(r"/promo_\w+"),
            )
        )
