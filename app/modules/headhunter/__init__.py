import asyncio
import logging

from config import HH
from core.module import BaseModule
from pyrogram import Client, filters, types
from pyrogram.handlers import MessageHandler

logger = logging.getLogger(__name__)


class HeadHunterModule(BaseModule):
    name = "headhunter"

    async def up(self, client: Client, message: types.Message):
        await asyncio.sleep(3)
        logger.info("Поднимаю резюме")
        await message.click(x=0, y=0)

    def register_handlers(self) -> None:
        self.app.add_handler(
            MessageHandler(
                self.up,
                filters.chat(HH) & filters.regex("Ваши резюме поднялись в поиске"),
            )
        )
