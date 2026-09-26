from dataclasses import replace

from app.engine.parsing import default_parser
from app.engine.parsing.smoothie import (
    SmoothieCooked,
    SmoothieCooking,
    SmoothieRecipe,
    SmoothieScreen,
    recipe_need,
    recognize_smoothie,
)
from app.engine.settings import ChatsSection
from tests.fixtures import game_msg, game_versions

FOOD_BONUS = "🍴На еде или фастфуде получаешь дополнительно +110%🔋 с шансом 75%."
FULL = {"lemon": 4, "grape": 4, "apple": 4, "carrot": 4, "tomato": 4}


def test_screen_with_and_without_current_bonus() -> None:
    assert recognize_smoothie(game_msg("smoothie", 3581573)) == [
        SmoothieScreen(ingredients=FULL, bonus=None)
    ]
    assert recognize_smoothie(game_msg("smoothie", 3523569)) == [
        SmoothieScreen(
            ingredients={"lemon": 3, "grape": 3, "apple": 3, "carrot": 4, "tomato": 2},
            bonus=FOOD_BONUS,
        )
    ]


def test_screen_needs_all_five_ingredients() -> None:
    msg = game_msg("smoothie", 3581573)
    assert msg.text is not None
    broken = replace(msg, text=msg.text.replace("🍇Виноград - 4 шт.\n", ""))
    assert recognize_smoothie(broken) == []


def test_cooked() -> None:
    assert recognize_smoothie(game_msg("smoothie", 3581575)) == [
        SmoothieCooked(recipe="🍇🥕🥕🍋🍅", bonus=FOOD_BONUS)
    ]


def test_cooking_screen_follows_drops() -> None:
    events = [recognize_smoothie(m) for m in game_versions("smoothie_cooking", 3625241)]
    assert events == [
        [SmoothieCooking(dropped="")],
        [SmoothieCooking(dropped="🍇")],
        [SmoothieCooking(dropped="🍇🥕")],
        [SmoothieCooking(dropped="🍇🥕🥕")],
        [SmoothieCooking(dropped="🍇🥕🥕🍋")],
        [SmoothieCooking(dropped="🍇🥕🥕🍋🍅")],
        [SmoothieCooked(recipe="🍇🥕🥕🍋🍅", bonus=None)],
    ]


def test_cooking_cancelled() -> None:
    [*_, cancelled] = game_versions("smoothie_cooking", 3625245)
    assert recognize_smoothie(cancelled) == [SmoothieCooking(cancelled=True)]


def test_channel_recipe_only_in_channel() -> None:
    parser = default_parser(ChatsSection())
    assert parser.parse(game_msg("smoothie", 2344)) == [
        SmoothieRecipe(recipe="🍇🥕🥕🍋🍅", bonus=FOOD_BONUS)
    ]
    assert parser.parse(game_msg("smoothie", 2343)) == [
        SmoothieRecipe(
            recipe="🍏🍋🍅🍇🍋", bonus="💡Получаешь на +50% больше Опыта в делах с шансом 75%."
        )
    ]


def test_recipe_text_in_game_chat_gives_no_recipe_event() -> None:
    chats = ChatsSection()
    parser = default_parser(chats)
    recipe = replace(game_msg("smoothie", 2344), chat_id=chats.game_chat_id)
    events = parser.parse(recipe)
    assert not any(isinstance(e, SmoothieRecipe) for e in events)


def test_recipe_need_counts_ingredients() -> None:
    assert recipe_need("🍇🥕🥕🍋🍅") == {"grape": 1, "carrot": 2, "lemon": 1, "tomato": 1}
