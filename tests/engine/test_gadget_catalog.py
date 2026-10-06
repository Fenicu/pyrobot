from app.engine.gadget_catalog import (
    BUYABLE_SETS,
    KIND_BY_CALLBACK,
    KIND_BY_ICON,
    SET_MIN_SLOTS,
    SET_SLOTS,
    SETS,
    SHOP,
    SLOTS,
    UPGRADE_KINDS,
    set_by_name,
    shop_item,
    slot_of_icon,
    slot_of_letter,
)


def test_shop_has_14_tiers_per_shop_slot() -> None:
    assert set(SHOP) == {"right", "left", "legs", "head", "chest", "torso"}
    assert all([i.tier for i in items] == list(range(1, 15)) for items in SHOP.values())


def test_live_showcase_right_matches_catalog() -> None:
    # живая витрина 09 (06.10): тир 1 $3 без требования, тир 7 - 26 ур. $2 519, тир 14 - 47 ур.
    right = SHOP["right"]
    assert (right[0].name, right[0].price, right[0].level) == ("Китайская мобила", 3, None)
    assert (right[6].name, right[6].level, right[6].price) == ("Чертёж телефона", 26, 2519)
    assert (right[13].name, right[13].bonuses, right[13].price) == (
        "GiftPhone",
        {"practice": 41, "theory": 21},
        59999,
    )
    assert SHOP["legs"][0].level == 10 and SHOP["legs"][0].required_level == 10


def test_shop_parts_of_buyable_sets_equal_shop_positions() -> None:
    for key in BUYABLE_SETS:
        s = SETS[key]
        assert s.shop_tier is not None
        for slot in ("right", "left", "legs", "head", "chest", "torso"):
            (part,) = s.items[slot]
            item = SHOP[slot][s.shop_tier - 1]
            assert (part.name.casefold(), dict(part.bonuses), part.level) == (
                item.name.casefold(),
                dict(item.bonuses),
                item.required_level,
            )


def test_ring_and_book_are_not_sold() -> None:
    assert SLOTS["ring"].shop is None and SLOTS["book"].shop is None


def test_set_ranks_and_lines() -> None:
    order = sorted(SETS.values(), key=lambda s: s.rank)
    assert [s.key for s in order] == [
        "summer",
        "autumn",
        "um",
        "pig",
        "y2020",
        "spring",
        "logistic",
    ]
    assert SETS["um"].line is None and SETS["pig"].line == "🐷Сет Свинтус"
    assert all(set(s.items) == set(SET_SLOTS) for s in SETS.values())
    assert len(SETS["logistic"].items["ring"]) == 2
    assert SET_MIN_SLOTS == 7
    assert BUYABLE_SETS == ("summer", "autumn", "um", "pig")
    assert [SETS[k].shop_tier for k in BUYABLE_SETS] == [11, 12, 13, 14]


def test_set_item_names_unique() -> None:
    names = [i.name.casefold() for s in SETS.values() for items in s.items.values() for i in items]
    assert len(names) == len(set(names))


def test_lookups() -> None:
    sm = shop_item("p11")
    assert sm is not None and sm.name == "S-март" and shop_item("t501") is None
    assert shop_item("h18") is None
    assert set_by_name("S-март") == (SETS["summer"], "right")
    assert set_by_name("LoRat") is None
    assert slot_of_icon("⌚") is slot_of_icon("⌚️") is SLOTS["left"]
    assert slot_of_letter("t") is SLOTS["torso"] and slot_of_letter("x") is None
    assert len({s.letter for s in SLOTS.values()}) == 10


def test_upgrade_kinds() -> None:
    assert UPGRADE_KINDS == {
        "white": ("⚪️", "low"),
        "blue": ("🔵", "middle"),
        "red": ("🔴", "high"),
    }
    assert KIND_BY_CALLBACK == {"low": "white", "middle": "blue", "high": "red"}
    assert KIND_BY_ICON["⚪"] == KIND_BY_ICON["⚪️"] == "white"
