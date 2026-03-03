import logging
from random import randint

from config import (
    GLADOS,
    MESA_MAIN,
    SMOOTHIE_BOT,
    STARTUP,
    STARTUP_MAIN,
    SWINFO,
    TANGERINE,
)
from pyrogram import Client, filters, types
from pyrogram.handlers import MessageHandler
from utils.sleep import sleep

logger = logging.getLogger(__name__)


async def just_click(client: Client, message: types.Message):
    await sleep()
    await message.click()


async def darts(client: Client, message: types.Message):
    await sleep(2)
    await client.send_message(STARTUP, "🎯 Дартс")


async def business_shark(client: Client, message: types.Message):
    await sleep()
    await message.click(0, randint(0, 1))


async def lotro(client: Client, message: types.Message):
    await sleep()
    await client.send_message(STARTUP, "/tickets_all")


async def virus_controller(client: Client, message: types.Message):
    await message.forward(SMOOTHIE_BOT)


async def fabrica_control(client: Client, message: types.Message = None):
    await sleep()
    await client.send_message(STARTUP, "/crew")
    await sleep()
    await client.send_message(STARTUP, "/crew_factory")
    await sleep()
    await client.send_message(STARTUP, "👍Записаться")


async def PP_control(client: Client, message: types.Message = None):
    await sleep()
    await client.send_message(STARTUP, "📯")


async def give_tangerine(client: Client):
    message_to_send = 927136  # Настя
    await client.send_message(TANGERINE, "/gt", reply_to_message_id=message_to_send)


async def go_to_birga(client: Client, message: types.Message):
    command = message.reply_markup.inline_keyboard[0][0].switch_inline_query
    await client.send_message(STARTUP, command)


def register_handlers(app: Client):
    app.add_handler(
        MessageHandler(
            just_click,
            filters.chat(STARTUP) & filters.regex("тебя начал грабить"),
        )
    )
    app.add_handler(
        MessageHandler(
            virus_controller,
            filters.chat(STARTUP)
            & filters.regex(r"Поздравляю! Твой вирус .* стал на \+\d+ сильнее \(\d+\)"),
        )
    )
    app.add_handler(
        MessageHandler(
            just_click,
            filters.chat(STARTUP) & filters.regex("🍹Смузийная"),
        )
    )
    app.add_handler(
        MessageHandler(
            darts,
            filters.chat(STARTUP)
            & filters.regex(r"(.*)Fenicu \(\d+\) берётся за дротики и кидает"),
        )
    )
    app.add_handler(
        MessageHandler(
            just_click,
            filters.user(GLADOS)
            & filters.chat(MESA_MAIN)
            & filters.regex(r"Запишись сегодня и получи награду уже через"),
        )
    )
    app.add_handler(
        MessageHandler(
            just_click,
            filters.user(GLADOS)
            & filters.chat(MESA_MAIN)
            & filters.regex(r"необоходимо срочно съесть"),
        )
    )
    app.add_handler(
        MessageHandler(
            just_click,
            filters.chat(STARTUP) & filters.regex("Ты у входа в давно заброшенные ветки 🚇Метро."),
        )
    )
    app.add_handler(
        MessageHandler(
            lotro,
            filters.user(SWINFO)
            & filters.chat(STARTUP_MAIN)
            & filters.regex("Приобрести билеты данного тиража вы можете"),
        )
    )
    app.add_handler(
        MessageHandler(
            business_shark,
            filters.user(SWINFO)
            & filters.chat(STARTUP_MAIN)
            & filters.regex("Тебе нужно лишь выбрать сторону конфликта:"),
        )
    )
    app.add_handler(
        MessageHandler(
            go_to_birga,
            filters.regex(
                r"Нажми на кнопку, выбери бота игры и отправь ему полученное сообщение."
            ),
        )
    )
