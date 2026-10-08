from dataclasses import replace

import pytest

from app.engine.events import Event
from app.engine.parsing.common import Rewards
from app.engine.parsing.items import (
    BookRead,
    CardUsed,
    ContainerOpened,
    Gadget,
    Gadgets,
    GiftsScreen,
    Inventory,
    PrizeboxOpened,
    recognize_items,
)
from tests.engine.inv_texts import INV_HISTORY, INV_PROD_1, INV_PROD_4, INV_PROD_5, INV_PROD_6
from tests.fixtures import game_msg


def _inventory(text: str) -> Inventory:
    msg = replace(game_msg("items", 3625102), text=text)
    [inventory] = recognize_items(msg)
    assert isinstance(inventory, Inventory)
    return inventory


# Во всех четырёх экранах ниже надет один и тот же набор: он совпадает с прод-образцом 1.
WORN = _inventory(INV_PROD_1).gadgets


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (
            3516676,
            Inventory(
                books=613,
                books_in_s=None,
                cards=62,
                cards_in_s=None,
                prizebox=False,
                prizebox_in_s=None,
                bag=10,
                bag_cap=24,
                gadgets=WORN,
            ),
        ),
        (
            3625102,
            Inventory(
                books=875,
                books_in_s=None,
                cards=13,
                cards_in_s=None,
                prizebox=True,
                prizebox_in_s=60480,
                bag=11,
                bag_cap=24,
                gadgets=WORN,
            ),
        ),
        (
            3595611,
            Inventory(
                books=785,
                books_in_s=None,
                cards=0,
                cards_in_s=None,
                prizebox=True,
                prizebox_in_s=54360,
                bag=11,
                bag_cap=24,
                gadgets=WORN,
            ),
        ),
        (
            3618363,
            Inventory(
                books=848,
                books_in_s=2280,
                cards=15,
                cards_in_s=2280,
                prizebox=True,
                prizebox_in_s=15660,
                bag=11,
                bag_cap=24,
                gadgets=WORN,
            ),
        ),
        (3516680, BookRead(exp=457, next_in_s=3000)),
        (3516678, CardUsed(money=597, next_in_s=3000)),
        (
            3516682,
            GiftsScreen(containers_small=0, containers_medium=0, tangerines=2, tangerine_gifts=0),
        ),
        (
            3623585,
            GiftsScreen(containers_small=5, containers_medium=0, tangerines=2, tangerine_gifts=0),
        ),
        (
            3611231,
            GiftsScreen(containers_small=0, containers_medium=1, tangerines=2, tangerine_gifts=0),
        ),
        (
            3517971,
            ContainerOpened(
                size="small",
                rewards=Rewards(
                    raw=2,
                    upgrades_white=3,
                    items={
                        "Флюс": 1,
                        "Пьезодинамик": 3,
                        "Датчик": 2,
                        "Микроконтроллер": 1,
                        "Наночип": 2,
                    },
                ),
            ),
        ),
        (
            # ⚙ с двойным VS16.
            3611233,
            ContainerOpened(
                size="medium",
                rewards=Rewards(
                    details=6,
                    upgrades_white=4,
                    upgrades_blue=1,
                    items={
                        "Нитки": 4,
                        "Пьезодинамик": 4,
                        "Кусок ткани": 1,
                        "Флюс": 4,
                        "Конденсатор": 1,
                        "Диод": 2,
                        "Молния": 1,
                        "Микроконтроллер": 2,
                        "Шнурок": 3,
                        "Мех": 1,
                    },
                ),
            ),
        ),
        (3517262, PrizeboxOpened(money_after=1108, rewards=Rewards(money=300))),
        (3625717, PrizeboxOpened(money_after=None, rewards=Rewards(exp=165))),
    ],
)
def test_items(msg_id: int, expected: Event) -> None:
    assert recognize_items(game_msg("items", msg_id)) == [expected]


def test_inventory_without_books_line_means_zero_books() -> None:
    msg = game_msg("items", 3625102)
    assert msg.text is not None
    no_books = replace(msg, text=msg.text.replace("📒Книга опыта: 875 /read_exp\n\n", ""))
    [inventory] = recognize_items(no_books)
    assert isinstance(inventory, Inventory)
    assert (inventory.books, inventory.books_in_s, inventory.cards) == (0, None, 13)


def test_inventory_without_slots_line_gives_nothing() -> None:
    msg = game_msg("items", 3625102)
    assert msg.text is not None
    broken = replace(msg, text=msg.text.replace("Занято 11 из 24", "…"))
    assert recognize_items(broken) == []


def test_gadgets_prod_1_field_by_field() -> None:
    gadgets = _inventory(INV_PROD_1).gadgets
    assert len(gadgets.items) == 10
    assert gadgets.items[0] == Gadget(
        grade="⚫️",
        level=26,
        slot="🕶",
        name="Хиджаб",
        bonuses={"theory": 85, "wisdom": 55, "practice": 30},
        mark="🧶",
        code="h18",
    )
    assert gadgets.items[7] == Gadget(
        grade="⚫️",
        level=25,
        slot="⌚️",
        name="SM-art",
        bonuses={"theory": 100, "practice": 51},
        mark="💎",
        code="w18",
    )
    slots = [g.slot for g in gadgets.items]
    assert slots == ["🕶", "👞", "👖", "👕", "📱", "💻", "💍", "⌚️", "🪫", "👔"]
    assert gadgets.sets == ("⚫️Сет VIP", "🗳Сет Логистик", "🗺Сет Кладоискатель", "🦉Сет Сова")


def test_gadgets_prod_4_without_mark_and_with_spaced_name() -> None:
    gadgets = _inventory(INV_PROD_4).gadgets
    assert len(gadgets.items) == 8
    assert gadgets.items[5] == Gadget(
        grade="🔴",
        level=18,
        slot="💻",
        name="MAC-адрес ноута",
        bonuses={"theory": 31, "cunning": 31},
        mark=None,
        code="b12",
    )
    assert all(g.mark is None for g in gadgets.items)
    assert gadgets.sets == ("🔴Сет Уникальный", "🌞Сет Летний")


def test_gadgets_prod_5_and_6() -> None:
    five = _inventory(INV_PROD_5).gadgets
    assert [g.name for g in five.items][:3] == ["PA’ltishCo", "Хулитопы", "SM-art"]
    assert five.sets == ("⚫️Сет VIP", "🗳Сет Логистик", "🗺Сет Кладоискатель")
    six = _inventory(INV_PROD_6).gadgets
    assert len(six.items) == 8
    assert six.items[6] == Gadget(
        grade="⚫️",
        level=25,
        slot="👔",
        name="Жилетка LoRat",
        bonuses={"wisdom": 110, "theory": 40},
        mark=None,
        code="t501",
    )
    assert six.sets == ("⚫️Сет VIP", "🐷Сет Свинтус")


def test_gadgets_history_sample_and_backpack_kept_apart() -> None:
    inventory = _inventory(INV_HISTORY)
    assert len(inventory.gadgets.items) == 10
    assert inventory.gadgets.items[2].slot == "👖"
    assert all("LoRat" not in g.name for g in inventory.gadgets.items[:9])
    assert (inventory.books, inventory.cards, inventory.bag, inventory.bag_cap) == (
        872,
        14,
        11,
        24,
    )
    assert [g.index for g in inventory.gadgets.bag] == list(range(1, 11))
    assert all("LoRat" in g.name for g in inventory.gadgets.bag)


def test_no_backpack_block_means_empty_bag() -> None:
    text = INV_PROD_1.split("Гаджеты в рюкзаке: (надеть)\n")[0] + "Занято 0 из 24"
    inventory = _inventory(text)
    assert inventory.gadgets.bag == () and not inventory.after_change
    assert len(inventory.gadgets.items) == 10


def test_backpack_lines_keep_index_and_code() -> None:
    bag = _inventory(INV_PROD_1).gadgets.bag
    assert len(bag) == 10
    assert (bag[0].index, bag[0].code, bag[0].slot, bag[0].grade) == (1, "t501", "👔", None)
    assert (bag[5].index, bag[5].grade, bag[5].level) == (6, "⚫️", 25)


def test_gadgets_separator_may_be_plain_space() -> None:
    assert "🔴18\xa0" in INV_PROD_4
    text = INV_PROD_4.replace("🔴18\xa0", "🔴18 ")
    assert _inventory(text).gadgets == _inventory(INV_PROD_4).gadgets


def test_gadgets_none_worn_and_no_sets() -> None:
    text = "Гаджеты при тебе: (снять)\n\nБонусы - /bonuses\n\n" + INV_PROD_4.split("\n\n", 2)[2]
    inventory = _inventory(text)
    assert (inventory.gadgets.items, inventory.gadgets.sets) == ((), ())
    assert (inventory.books, inventory.bag_cap) == (131, 20)


def test_broken_gadget_line_is_skipped_inventory_still_parsed() -> None:
    text = INV_PROD_4.replace(
        "🔴18\xa0💍Bat Ring (+31🔨, +31🐢) /unwear_r12", "🔴?? мусор /unwear_r12"
    )
    inventory = _inventory(text)
    assert len(inventory.gadgets.items) == 7
    assert "Bat Ring" not in [g.name for g in inventory.gadgets.items]
    assert inventory.gadgets.sets == ("🔴Сет Уникальный", "🌞Сет Летний")
    assert (inventory.books, inventory.cards, inventory.bag, inventory.bag_cap) == (
        131,
        40,
        12,
        20,
    )


_TAIL = "Занято 0 из 20\n"


def test_sets_stop_at_first_non_set_line_without_bonuses_line() -> None:
    text = (
        "Гаджеты при тебе: (снять)\n⚫️25\xa0👔Жилетка LoRat (+110🐢, +40🎓) /unwear_t501\n\n"
        "⚫️Сет VIP\n🌞Сет Летний\nТвои ресурсы - 1 шт.\nПосмотреть - /bag\n\n" + _TAIL
    )
    assert _inventory(text).gadgets.sets == ("⚫️Сет VIP", "🌞Сет Летний")


def test_sets_none_when_next_line_is_foreign() -> None:
    text = (
        "Гаджеты при тебе: (снять)\n⚫️25\xa0👔Жилетка LoRat (+110🐢, +40🎓) /unwear_t501\n\n"
        "Твои ресурсы - 1 шт.\nПосмотреть - /bag\n\n" + _TAIL
    )
    assert _inventory(text).gadgets.sets == ()


def test_empty_screen_without_bonuses_line_has_no_items_and_sets() -> None:
    text = "Гаджеты при тебе: (снять)\n\nТвои ресурсы - 1 шт.\nПосмотреть - /bag\n\n" + _TAIL
    assert _inventory(text).gadgets == Gadgets()


def test_worn_gadget_without_grade_and_level() -> None:
    text = (
        "Гаджеты при тебе: (снять)\n👔Жилетка LoRat (+63🐢, +23🎓) /unwear_t501\n"
        "💍Простое кольцо (+10🔨, +10🐢) /unwear_r10\n\nБонусы - /bonuses\n\n" + _TAIL
    )
    first, second = _inventory(text).gadgets.items
    assert first == Gadget(
        grade=None,
        level=None,
        slot="👔",
        name="Жилетка LoRat",
        bonuses={"wisdom": 63, "theory": 23},
        code="t501",
    )
    assert (second.grade, second.level, second.slot, second.name) == (
        None,
        None,
        "💍",
        "Простое кольцо",
    )
