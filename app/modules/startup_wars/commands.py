import logging

from pyrogram import Client, filters, types
from pyrogram.handlers import MessageHandler

logger = logging.getLogger(__name__)


async def id_info(client: Client, message: types.Message):
    out = ""
    if message.reply_to_message:
        out += f"reply_id: {message.reply_to_message.from_user.id}\n"
    if message.forward_from:
        try:
            out += f"forward_id: {message.forward_from.id}\n"
        except Exception:
            out += "forward_id: hidden\n"
    if message.chat.id != message.from_user.id:
        out += f"chat_id: {message.chat.id}\n"
    await message.edit_text(out)


async def keyboard_info(client: Client, message: types.Message):
    kb = message.reply_to_message.reply_markup
    out = ""
    for idx, row in enumerate(kb.inline_keyboard):
        out += f"Строка {idx}\n"
        for button in row:
            data = button.callback_data or button.url or button.switch_inline_query
            out += f"{button.text}: {data}\n"
    logger.debug(kb.inline_keyboard)
    await message.edit_text(out)


def register_handlers(app: Client):
    app.add_handler(
        MessageHandler(
            id_info,
            filters.user("me") & filters.regex("!id"),
        )
    )
    app.add_handler(
        MessageHandler(
            keyboard_info,
            filters.user("me") & filters.regex(r"ковальски,? анализ") & filters.reply,
        )
    )
