from dataclasses import replace

from app.engine.parsing.food import FastfoodEaten, FoodMenu, FoodStock, recognize_food
from tests.fixtures import game_msg


def test_food_menu_without_cooldown() -> None:
    assert recognize_food(game_msg("food", 3521844)) == [
        FoodMenu(
            stamina=0,
            stock={
                "hotdog": FoodStock(count=8390, low=50, high=140),
                "pizza": FoodStock(count=4852, low=70, high=160),
                "burger": FoodStock(count=3548, low=90, high=180),
                "banana": FoodStock(count=0, low=150, high=275),
            },
            fastfood_in_s=None,
        )
    ]


def test_food_menu_with_cooldown() -> None:
    events = recognize_food(game_msg("food", 3624997))
    assert len(events) == 1 and isinstance(events[0], FoodMenu)
    assert (events[0].stamina, events[0].fastfood_in_s) == (52, 1740)
    assert events[0].stock["banana"] == FoodStock(count=12, low=150, high=275)


def test_partial_menu_gives_nothing() -> None:
    msg = game_msg("food", 3521844)
    assert msg.text is not None
    broken = replace(msg, text=msg.text.replace("🍕\xa0Пицца (🔋 70-160%): 4852 шт.", "…"))
    assert recognize_food(broken) == []


def test_fastfood_eaten() -> None:
    assert recognize_food(game_msg("food", 3522307)) == [
        FastfoodEaten(food="burger", stamina=93, motivation=0)
    ]
    assert recognize_food(game_msg("food", 3536881)) == [
        FastfoodEaten(food="banana", stamina=226, motivation=1)
    ]
