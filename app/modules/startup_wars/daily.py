import logging
import re

from config import STARTUP, TEAMCHAT
from pyrogram import Client, filters, types
from pyrogram.handlers import MessageHandler
from utils.sleep import sleep

logger = logging.getLogger(__name__)

TARGET_HARVEST = "/harvest"


class DailyQuests:
    def __init__(self):
        self.stop_daily = False

    async def execute_or_harvest(self, client: Client, command: str):
        await sleep()
        target = f"/{command}" if not self.stop_daily else TARGET_HARVEST
        await client.send_message(STARTUP, target)

    async def harvest(self, client: Client, message: types.Message):
        await sleep()
        await client.send_message(STARTUP, TARGET_HARVEST)

    async def daily_controller(self, client: Client, message: types.Message):
        self.stop_daily = True
        await message.forward(TEAMCHAT)
        await sleep(100)
        self.stop_daily = False

    async def dconv(self, client: Client, message: types.Message):
        await self.execute_or_harvest(client, "dconv")

    async def walk(self, client: Client, message: types.Message):
        await self.execute_or_harvest(client, "walk")

    async def job(self, client: Client, message: types.Message):
        await self.execute_or_harvest(client, "job")

    async def learns(self, client: Client, message: types.Message):
        await self.execute_or_harvest(client, "learns")

    async def confa(self, client: Client, message: types.Message):
        await self.execute_or_harvest(client, "confa")

    async def dos(self, client: Client, message: types.Message):
        await sleep()
        await client.send_message(STARTUP, "/dos")

    async def resk(self, client: Client, message: types.Message):
        lab = re.search(r"Продолжить 📚изучение - /resk(\d+)", message.text).group(1)
        await sleep()
        await client.send_message(STARTUP, f"/resk{lab}")

    async def resm(self, client: Client, message: types.Message):
        lab = re.search(r"Продолжить 🔩разработку - /resm(\d+)", message.text).group(1)
        await sleep()
        await client.send_message(STARTUP, f"/resm{lab}")

    async def balance_control(self, client: Client, message: types.Message):
        await sleep()
        await client.send_message(STARTUP, "/job")
        await sleep(330)
        await client.send_message(STARTUP, TARGET_HARVEST)

    def register_handlers(self, app: Client):
        app.add_handler(
            MessageHandler(
                self.harvest,
                filters.chat(STARTUP) & filters.regex("Мотивация полностью восстановлена"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.harvest,
                filters.chat(STARTUP) & filters.regex("Продолжить ⛏Добычу - /harvest"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.daily_controller,
                filters.chat(STARTUP) & filters.regex("Ты завершил задание в команде и заработал"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.dconv,
                filters.chat(STARTUP) & filters.regex("ещё - /dconv"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.walk,
                filters.chat(STARTUP) & filters.regex("Продолжить 🚶Гулять - /walk"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.job,
                filters.chat(STARTUP) & filters.regex("Продолжить 💻Работать - /job"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.learns,
                filters.chat(STARTUP) & filters.regex("📚Учиться ещё - /learns"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.dos,
                filters.chat(STARTUP) & filters.regex("🖥 Пилить стартап дальше - /dos"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.confa,
                filters.chat(STARTUP) & filters.regex("На новую 📚Конфу - /confa"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.resk,
                filters.chat(STARTUP) & filters.regex(r"Продолжить 📚изучение - /resk(\d+)"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.resm,
                filters.chat(STARTUP) & filters.regex(r"Продолжить 🔩разработку - /resm(\d+)"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.balance_control,
                filters.chat(STARTUP) & filters.regex("Сначала заработай, потом трать"),
            )
        )
