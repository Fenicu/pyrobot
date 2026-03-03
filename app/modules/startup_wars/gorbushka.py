import logging
import re
from contextlib import suppress

from config import STARTUP, SWCLOCK
from pyrogram import Client, filters, types
from pyrogram.handlers import EditedMessageHandler, MessageHandler
from utils.sleep import sleep

logger = logging.getLogger(__name__)


async def food_control(client: Client, message: types.Message = None):
    await sleep()
    await client.send_message(STARTUP, "/eat")


async def gorbushka_control(client: Client, message: types.Message = None):
    await sleep()
    await client.send_message(STARTUP, "/gorbushka")


async def go_to_gorbushka(client: Client, message: types.Message):
    await sleep()
    await client.send_message(STARTUP, "/gorbushka")


async def go_to_prodavan(client: Client, message: types.Message):
    await sleep()
    if "Ты встретишь следующего 👨Продавана" in message.text:
        return
    if "Приходи через" in message.text:
        return

    if "Выносливость" in message.text:
        try:
            stamina = int(re.search(r"🔋Твоя выносливость: (\d+)%", message.text).group(1))
        except AttributeError:
            stamina = int(re.search(r"🔋Выносливость: (\d+)%", message.text).group(1))
        except Exception:
            logger.exception("Failed to parse stamina")
            stamina = 100
        if stamina < 100:
            await client.send_message(STARTUP, "/eat")
            await sleep()

    with suppress(Exception):
        await message.click()


def register_handlers(app: Client):
    app.add_handler(
        MessageHandler(
            go_to_prodavan,
            filters.chat(STARTUP) & filters.regex("🏛Горбушка, сэр"),
        )
    )
    app.add_handler(
        EditedMessageHandler(
            go_to_prodavan,
            filters.chat(STARTUP) & filters.regex("🏛Горбушка, сэр"),
        )
    )
    app.add_handler(
        MessageHandler(
            go_to_gorbushka,
            filters.chat(SWCLOCK) & filters.regex("👨Продаваны готовы к битве"),
        )
    )
    app.add_handler(
        MessageHandler(
            food_control,
            filters.chat(STARTUP) & filters.regex("🔋Выносливость: 0%"),
        )
    )
