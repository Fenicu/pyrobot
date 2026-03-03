import asyncio
import random


async def sleep(delta: int | None = None):
    if delta is None:
        delta = random.randint(3, 7)
    await asyncio.sleep(delta)


def fetch_buttons(
    message,
    lookup_dict: dict[str, str],
) -> dict[str, dict[str, str]]:
    buttons = {}
    for row in message.reply_markup.inline_keyboard:
        for cell in row:
            button_key = lookup_dict.get(cell.callback_data)
            if button_key is not None:
                buttons[button_key] = {
                    "text": cell.text,
                    "callback_data": cell.callback_data,
                }
    return buttons
