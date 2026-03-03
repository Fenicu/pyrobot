import logging
import re

from config import SMOOTHIE, STARTUP
from pyrogram import Client, filters, types
from pyrogram.handlers import EditedMessageHandler, MessageHandler
from utils.sleep import sleep

logger = logging.getLogger(__name__)

SMOOTHIE_DICT = {
    "🍋": "sm_drop_1",
    "🍇": "sm_drop_2",
    "🍏": "sm_drop_3",
    "🥕": "sm_drop_4",
    "🍅": "sm_drop_5",
    "done": "smoothie_accept",
}
SMOOTHIE_REGEXP = re.compile(r"^Рецепт: (?P<recipe>.*)$", re.MULTILINE | re.IGNORECASE)


class SmoothieCrafter:
    def __init__(self):
        self.items: list[str] | None = None

    def _smoothie_items_filter(self, *args, **kwargs):
        return bool(self.items)

    async def smoothie_mesa(self, client: Client, message: types.Message):
        await sleep()
        recipe = SMOOTHIE_REGEXP.search(message.text).group("recipe")
        new_list = list(recipe)
        new_list.append("done")
        new_list.reverse()
        self.items = new_list
        await client.send_message(STARTUP, "/smoothie")

    async def cook_smoothie(self, client: Client, message: types.Message):
        await sleep()
        if not self.items:
            return
        if len(self.items) <= 0:
            self.items = None
            await message.click()
            return
        item = self.items.pop()
        try:
            await client.request_callback_answer(message.chat.id, message.id, SMOOTHIE_DICT[item])
        except Exception:
            logger.exception("Failed to click smoothie ingredient")

    async def smoothie_control(self, client: Client, message: types.Message):
        await sleep()
        await client.send_message(STARTUP, "/smoothie")

    def register_handlers(self, app: Client):
        smoothie_items_filter = filters.create(self._smoothie_items_filter)

        app.add_handler(
            MessageHandler(
                self.smoothie_mesa,
                filters.chat(SMOOTHIE) & filters.regex("Рецепт"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.cook_smoothie,
                filters.chat(STARTUP) & filters.regex("🍹Готовлю"),
            )
        )
        app.add_handler(
            EditedMessageHandler(
                self.cook_smoothie,
                filters.chat(STARTUP) & filters.regex("🍹Готовлю"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.smoothie_control,
                filters.chat(STARTUP)
                & filters.regex("К персонажу - /main.")
                & smoothie_items_filter,
            )
        )
