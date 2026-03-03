import json as jsonlib
import logging
import re
from urllib.parse import urljoin

from config import METRO_API_URL, STARTUP
from core.http_client import get_session
from pyrogram import Client, filters, types
from pyrogram.handlers import EditedMessageHandler, MessageHandler
from utils.sleep import fetch_buttons, sleep

logger = logging.getLogger(__name__)

METRO_DECIDER = r"⬛️.*⬛️"
METRO_REGEX = re.compile(METRO_DECIDER, re.DOTALL)
MOVING_REGEX = re.compile(r"Идёшь")
PROFILE_REGEX = re.compile(r"(Полный профиль)|(Компактный профиль)")

BUFFS_BUTTONS_LOOKUP = {
    "speed": "maze_buf_tokens_fastMove",
    "donate_speed": "maze_buf_coins_fastMove",
    "strength": "maze_buf_tokens_strong",
    "donate_strength": "maze_buf_coins_strong",
    "first_aid": "maze_buf_tokens_firstAid",
    "donate_first_aid": "maze_buf_coins_firstAid",
    "start": "maze_start",
}

MAP_BUTTONS_LOOKUP = {
    "maze_up": "up",
    "maze_down": "down",
    "maze_left": "left",
    "maze_right": "right",
    "maze_first_aid": "health_pack",
}

OK_BUTTON_LOOKUP = {"maze_continue": "ok"}

HEALTHPACK_BUTTONS_LOOKUP = {
    "maze_first_aid_accept": "ok",
    "maze_first_aid_decline": "cancel",
}

CHEST_BUTTONS_LOOKUP = {"maze_chest_accept": "ok", "maze_chest_decline": "cancel"}
DOOR_BUTTONS_LOOKUP = {"maze_exit_accept": "ok", "maze_exit_decline": "cancel"}

NPC_BUTTONS_LOOKUP = {
    "maze_npc_low_accept": "ok",
    "maze_npc_low_decline": "cancel",
    "maze_npc_high_accept": "ok",
    "maze_npc_high_decline": "cancel",
}


class MetroGame:
    def __init__(self):
        self.tracked_message_id: int | None = None
        self.my_id: int | None = None
        self.in_metro: bool = False

    def _metro_filter(self, *args, **kwargs):
        return self.in_metro

    async def send_simple_request(self, additional_url: str, data: dict) -> str | None:
        url = urljoin(METRO_API_URL, additional_url)
        logger.debug("Sending to %s %s", additional_url, data)
        session = get_session()
        try:
            async with session.post(url, json=data) as resp:
                if resp.status != 200:
                    logger.warning("Server failed %s %s", resp.status, await resp.text())
                    self.tracked_message_id = None
                    return None
                result = await resp.text()
        except Exception:
            logger.exception("HTTP request to %s failed", additional_url)
            self.tracked_message_id = None
            return None
        logger.debug(result)
        return result

    async def send_request(self, additional_url: str, data: dict, client: Client) -> dict | None:
        logger.debug("Sending #%s to %s", data, additional_url)
        answer = await self.send_simple_request(additional_url, data)
        if answer is None:
            return None
        answer = jsonlib.loads(answer)
        callback_data = answer.get("callback_data")
        if callback_data is not None:
            await sleep(1)
            try:
                await client.request_callback_answer(
                    "@StartupWarsBot", self.tracked_message_id, callback_data
                )
            except TimeoutError:
                pass
        return answer

    async def process_typical_message(
        self,
        additional_url: str,
        message: types.Message,
        buttons: dict,
        client: Client,
    ):
        if message.id == self.tracked_message_id:
            data = {
                "text": message.text,
                "my_id": self.my_id,
                "message_id": self.tracked_message_id,
                "buttons": buttons,
            }
            await self.send_request(additional_url, data, client)

    async def search_for_metro(self, client: Client) -> bool:
        async for message in client.get_chat_history(chat_id="@StartupWarsBot", limit=100):
            if METRO_REGEX.search(message.text):
                self.tracked_message_id = message.id
                logger.debug(
                    "my id is %s and tracking message id %s",
                    self.my_id,
                    self.tracked_message_id,
                )
                await self.process_typical_message(
                    "map",
                    message,
                    fetch_buttons(message, MAP_BUTTONS_LOOKUP),
                    client,
                )
                return True
        logger.warning("No metro found")
        return False

    async def metro_purchase(self, client: Client, message: types.Message):
        self.in_metro = True
        await sleep()
        await client.request_callback_answer(
            STARTUP, message.id, BUFFS_BUTTONS_LOOKUP["first_aid"]
        )
        await sleep()
        await client.request_callback_answer(STARTUP, message.id, BUFFS_BUTTONS_LOOKUP["start"])

    async def metro_exit(self, client: Client, message: types.Message):
        self.in_metro = False
        await sleep()
        await client.send_message(STARTUP, "/main")

    async def map_handler(self, client: Client, message: types.Message):
        if not MOVING_REGEX.search(message.text):
            await self.process_typical_message(
                "map", message, fetch_buttons(message, MAP_BUTTONS_LOOKUP), client
            )

    async def npc_handler(self, client: Client, message: types.Message):
        await self.process_typical_message(
            "npc", message, fetch_buttons(message, NPC_BUTTONS_LOOKUP), client
        )

    async def npc_handled_handler(self, client: Client, message: types.Message):
        await self.process_typical_message(
            "npc_handled", message, fetch_buttons(message, OK_BUTTON_LOOKUP), client
        )

    async def chest_handler(self, client: Client, message: types.Message):
        await self.process_typical_message(
            "chest", message, fetch_buttons(message, CHEST_BUTTONS_LOOKUP), client
        )

    async def chest_handled_handler(self, client: Client, message: types.Message):
        await self.process_typical_message(
            "chest_handled", message, fetch_buttons(message, OK_BUTTON_LOOKUP), client
        )

    async def health_pack_handler(self, client: Client, message: types.Message):
        await self.process_typical_message(
            "health_pack",
            message,
            fetch_buttons(message, HEALTHPACK_BUTTONS_LOOKUP),
            client,
        )

    async def health_pack_handled_handler(self, client: Client, message: types.Message):
        await self.process_typical_message(
            "health_pack_handled",
            message,
            fetch_buttons(message, OK_BUTTON_LOOKUP),
            client,
        )

    async def door_handler(self, client: Client, message: types.Message):
        await self.process_typical_message(
            "door", message, fetch_buttons(message, DOOR_BUTTONS_LOOKUP), client
        )

    async def loot_handler(self, client: Client, message: types.Message):
        await self.process_typical_message(
            "loot", message, fetch_buttons(message, OK_BUTTON_LOOKUP), client
        )

    async def save_handler(self, client: Client, message: types.Message):
        url = urljoin(METRO_API_URL, "dump_map")
        session = get_session()
        try:
            async with session.post(url) as resp:
                if resp.status != 200:
                    logger.warning("Server failed to dump map")
        except Exception:
            logger.exception("Failed to dump map")

    async def dump_map_handler(self, client: Client, message: types.Message):
        url = urljoin(METRO_API_URL, "save_tmp")
        session = get_session()
        try:
            async with session.post(url) as resp:
                if resp.status != 200:
                    logger.warning("Server failed to save tmp")
        except Exception:
            logger.exception("Failed to save tmp")

    async def continue_handler(self, client: Client, message: types.Message):
        self.my_id = message.from_user.id
        await self.search_for_metro(client)

    async def start_handler(self, client: Client, message: types.Message):
        async for msg in client.get_chat_history(chat_id="@StartupWarsBot", limit=200):
            if PROFILE_REGEX.search(msg.text):
                self.my_id = msg.from_user.id
                data = {"text": msg.text, "my_id": self.my_id}
                if (await self.send_simple_request("profile", data)) is not None:
                    await self.search_for_metro(client)
                    return
        logger.debug("No profile found")

    def register_handlers(self, app: Client):
        metro_filter = filters.create(self._metro_filter)  # noqa: F841 — used below

        app.add_handler(
            MessageHandler(
                self.metro_exit,
                filters.chat(STARTUP) & filters.regex("К персонажу - /main."),
            )
        )
        app.add_handler(
            MessageHandler(
                self.save_handler,
                filters.user("me") & filters.regex("!metro dump map"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.dump_map_handler,
                filters.user("me") & filters.regex("!metro save"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.continue_handler,
                filters.user("me") & filters.regex("!metro continue moved"),
            )
        )
        app.add_handler(
            MessageHandler(
                self.start_handler,
                filters.user("me") & filters.regex("!metro"),
            )
        )
        app.add_handler(
            EditedMessageHandler(
                self.start_handler,
                filters.chat(STARTUP) & filters.regex("😎") & filters.regex("Вход"),
            )
        )
        app.add_handler(
            EditedMessageHandler(
                self.metro_purchase,
                filters.chat(STARTUP) & filters.regex("Бафы для метро"),
            )
        )
        app.add_handler(
            EditedMessageHandler(
                self.map_handler,
                filters.chat(STARTUP) & filters.regex(METRO_DECIDER, re.DOTALL),
            )
        )
        app.add_handler(
            EditedMessageHandler(
                self.npc_handler,
                filters.chat(STARTUP) & filters.regex("Ты нашёл.*Продавана"),
            )
        )
        app.add_handler(
            EditedMessageHandler(
                self.npc_handled_handler,
                filters.chat(STARTUP) & filters.regex("Ты сразился с .*Продаваном "),
            )
        )
        app.add_handler(
            EditedMessageHandler(
                self.chest_handler,
                filters.chat(STARTUP) & filters.regex("Ты нашёл большой .*Сундук"),
            )
        )
        app.add_handler(
            EditedMessageHandler(
                self.chest_handled_handler,
                filters.chat(STARTUP) & filters.regex("Ты потихоньку открыл 📦Сундук."),
            )
        )
        app.add_handler(
            EditedMessageHandler(
                self.health_pack_handler,
                filters.chat(STARTUP) & filters.regex("Используешь аптечку?"),
            )
        )
        app.add_handler(
            EditedMessageHandler(
                self.health_pack_handled_handler,
                filters.chat(STARTUP) & filters.regex("Используешь аптечку?"),
            )
        )
        app.add_handler(
            EditedMessageHandler(
                self.door_handler,
                filters.chat(STARTUP) & filters.regex("Ты нашёл выход из метро! "),
            )
        )
        app.add_handler(
            EditedMessageHandler(
                self.loot_handler,
                filters.chat(STARTUP) & (filters.regex("Н|нашёл") | filters.regex("открыл")),
            )
        )
