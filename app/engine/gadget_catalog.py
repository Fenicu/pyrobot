from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

UpSlot = Literal[
    "right", "left", "legs", "head", "chest", "torso", "ring", "book", "pbank", "pants"
]
ShopSlot = Literal["right", "left", "legs", "head", "chest", "torso"]
SetKey = Literal["summer", "autumn", "um", "pig", "y2020", "spring", "logistic"]
UpgradeKind = Literal["white", "blue", "red"]

_VS16 = "\ufe0f"


@dataclass(frozen=True, slots=True)
class SlotInfo:
    icon: str
    letter: str
    up: UpSlot
    shop: ShopSlot | None
    button: str | None
    min_level: int | None


SLOTS: dict[UpSlot, SlotInfo] = {
    "right": SlotInfo("📱", "p", "right", "right", "📱Правая рука", 1),
    "left": SlotInfo("⌚", "w", "left", "left", "⌚️Левая рука", 1),
    "legs": SlotInfo("👞", "l", "legs", "legs", "👞Ноги", 10),
    "head": SlotInfo("🕶", "h", "head", "head", "🕶Голова", 10),
    "chest": SlotInfo("👕", "c", "chest", "chest", "👕Грудь", 20),
    "torso": SlotInfo("👔", "t", "torso", "torso", "👔Тело", 20),
    "ring": SlotInfo("💍", "r", "ring", None, None, None),
    "book": SlotInfo("💻", "b", "book", None, None, None),
    "pbank": SlotInfo("🪫", "k", "pbank", None, None, None),
    "pants": SlotInfo("👖", "s", "pants", None, None, None),
}
_BY_ICON = {s.icon: s for s in SLOTS.values()}
_BY_LETTER = {s.letter: s for s in SLOTS.values()}


def slot_of_icon(icon: str) -> SlotInfo | None:
    return _BY_ICON.get(icon.replace(_VS16, ""))


def slot_of_letter(letter: str) -> SlotInfo | None:
    return _BY_LETTER.get(letter)


@dataclass(frozen=True, slots=True)
class ShopItem:
    slot: ShopSlot
    tier: int
    name: str
    bonuses: Mapping[str, int]
    level: int | None
    price: int

    @property
    def code(self) -> str:
        return f"{SLOTS[self.slot].letter}{self.tier}"

    @property
    def required_level(self) -> int:
        return max(self.level or 1, SLOTS[self.slot].min_level or 1)


SHOP: dict[ShopSlot, tuple[ShopItem, ...]] = {
    "right": (
        ShopItem("right", 1, "Китайская мобила", {"practice": 1}, None, 3),
        ShopItem("right", 2, "Китайский смартфон", {"practice": 3}, None, 29),
        ShopItem("right", 3, "Корейская мобила", {"practice": 5}, None, 124),
        ShopItem("right", 4, "Корейский смартфон", {"practice": 7}, None, 299),
        ShopItem("right", 5, "яМобилка", {"practice": 9}, None, 629),
        ShopItem("right", 6, "Nokia 3310", {"practice": 11, "theory": 3}, None, 1399),
        ShopItem("right", 7, "Чертёж телефона", {"practice": 13, "theory": 5}, 26, 2519),
        ShopItem("right", 8, "Hooli phone", {"practice": 17, "theory": 7}, 29, 4449),
        ShopItem("right", 9, "Мобила-X", {"practice": 11, "theory": 21}, 32, 9279),
        ShopItem("right", 10, "HooliPhone PRO", {"practice": 25, "theory": 13}, 35, 13849),
        ShopItem("right", 11, "S-март", {"practice": 29, "theory": 15}, 38, 31999),
        ShopItem("right", 12, "Tullp Phone", {"practice": 33, "theory": 17}, 41, 39999),
        ShopItem("right", 13, "Um-Phone", {"practice": 37, "theory": 19}, 44, 49999),
        ShopItem("right", 14, "GiftPhone", {"practice": 41, "theory": 21}, 47, 59999),
    ),
    "left": (
        ShopItem("left", 1, "Пластмассовые часы", {"theory": 1}, None, 3),
        ShopItem("left", 2, "Часы Montana", {"theory": 3}, None, 29),
        ShopItem("left", 3, "Поддельные яЧасики", {"theory": 5}, None, 124),
        ShopItem("left", 4, "Поддельный Rolex", {"theory": 7}, None, 299),
        ShopItem("left", 5, "яЧасики", {"theory": 9}, None, 629),
        ShopItem("left", 6, "Часы Rolex", {"theory": 11, "practice": 3}, None, 1399),
        ShopItem("left", 7, "Часы HelloKitty", {"theory": 13, "practice": 5}, 26, 2519),
        ShopItem("left", 8, "Umbrolex", {"theory": 17, "practice": 7}, 29, 4449),
        ShopItem("left", 9, "Часы-X", {"theory": 11, "practice": 21}, 32, 9279),
        ShopItem("left", 10, "Часы-наклейка", {"theory": 25, "practice": 13}, 35, 13849),
        ShopItem("left", 11, "S-ейчас", {"theory": 29, "practice": 15}, 38, 31999),
        ShopItem("left", 12, "U-Time", {"theory": 33, "practice": 17}, 41, 39999),
        ShopItem("left", 13, "Um-Watch", {"theory": 37, "practice": 19}, 44, 49999),
        ShopItem("left", 14, "Drink Time", {"theory": 41, "practice": 21}, 47, 59999),
    ),
    "legs": (
        ShopItem("legs", 1, "Термо-носки", {"practice": 1, "cunning": 1}, 10, 9),
        ShopItem("legs", 2, "Термо-штаны", {"practice": 2, "cunning": 2}, 11, 79),
        ShopItem("legs", 3, "Термо-ботинки", {"practice": 4, "cunning": 3}, 12, 314),
        ShopItem("legs", 4, "Умные ботинки", {"practice": 6, "cunning": 4}, 13, 699),
        ShopItem("legs", 5, "яБотинки", {"practice": 9, "cunning": 5}, 14, 1229),
        ShopItem("legs", 6, "Берцы", {"practice": 12, "cunning": 7, "theory": 3}, 15, 2849),
        ShopItem(
            "legs", 7, "Луи Зонтон Бутс", {"practice": 15, "cunning": 9, "theory": 5}, 27, 5219
        ),
        ShopItem(
            "legs", 8, "Голограмма ноги", {"practice": 18, "cunning": 11, "theory": 7}, 30, 8819
        ),
        ShopItem("legs", 9, "Боты-X", {"theory": 21, "wisdom": 13, "practice": 8}, 33, 13649),
        ShopItem(
            "legs", 10, "HooliBoots", {"practice": 24, "cunning": 15, "theory": 9}, 36, 19899
        ),
        ShopItem("legs", 11, "S-ланцы", {"practice": 27, "cunning": 17, "theory": 10}, 39, 44499),
        ShopItem(
            "legs", 12, "Rise of RoBoots", {"practice": 30, "cunning": 19, "theory": 11}, 42, 59999
        ),
        ShopItem(
            "legs", 13, "Подкаблучники", {"practice": 33, "cunning": 21, "theory": 12}, 45, 73999
        ),
        ShopItem(
            "legs", 14, "Bat-Galoshi", {"practice": 36, "cunning": 23, "theory": 13}, 48, 82999
        ),
    ),
    "head": (
        ShopItem("head", 1, "Очки", {"theory": 1, "wisdom": 1}, 10, 9),
        ShopItem("head", 2, "Очки с диоптриями", {"theory": 2, "wisdom": 2}, 11, 79),
        ShopItem("head", 3, "Монокль", {"theory": 4, "wisdom": 3}, 12, 314),
        ShopItem("head", 4, "Бинокль", {"theory": 6, "wisdom": 4}, 13, 699),
        ShopItem("head", 5, "яОчки", {"theory": 9, "wisdom": 5}, 14, 1229),
        ShopItem("head", 6, "VR шлем", {"theory": 12, "wisdom": 7, "practice": 3}, 15, 2849),
        ShopItem("head", 7, "Piper Glass", {"theory": 15, "wisdom": 9, "practice": 5}, 27, 5219),
        ShopItem("head", 8, "Шапка-ушанка", {"theory": 18, "wisdom": 11, "practice": 7}, 30, 8819),
        ShopItem("head", 9, "Шляпа-X", {"practice": 21, "cunning": 13, "theory": 8}, 33, 13649),
        ShopItem("head", 10, "Red Hat", {"theory": 24, "wisdom": 15, "practice": 9}, 36, 19899),
        ShopItem("head", 11, "S-глаз", {"theory": 27, "wisdom": 17, "practice": 10}, 39, 44499),
        ShopItem(
            "head", 12, "Линзы-Hooliнзы", {"theory": 30, "wisdom": 19, "practice": 11}, 42, 59999
        ),
        ShopItem(
            "head", 13, "Концепт VRшлема", {"theory": 33, "wisdom": 21, "practice": 12}, 45, 73999
        ),
        ShopItem(
            "head", 14, "Головограмма", {"theory": 36, "wisdom": 23, "practice": 13}, 48, 82999
        ),
    ),
    "chest": (
        ShopItem("chest", 1, "Простая майка", {"cunning": 3}, 20, 29),
        ShopItem("chest", 2, "Майка-алкоголичка", {"cunning": 6}, 21, 269),
        ShopItem("chest", 3, "Майка с принтом", {"cunning": 9}, 22, 629),
        ShopItem("chest", 4, "Умная майка", {"cunning": 12}, 23, 1139),
        ShopItem("chest", 5, "яМайка", {"cunning": 15}, 24, 1949),
        ShopItem("chest", 6, "Тельняшка", {"cunning": 19, "practice": 5}, 25, 3999),
        ShopItem("chest", 7, "Hooli T-Shirt", {"cunning": 23, "practice": 7}, 28, 6449),
        ShopItem("chest", 8, "Кофта АнтиХайп", {"cunning": 28, "practice": 9}, 31, 9999),
        ShopItem("chest", 9, "Майка-X", {"wisdom": 33, "theory": 11}, 34, 14999),
        ShopItem("chest", 10, "Кофта СМП", {"cunning": 38, "practice": 13}, 37, 21674),
        ShopItem("chest", 11, "S-орочка", {"cunning": 43, "practice": 15}, 40, 47999),
        ShopItem("chest", 12, "Arc Reactor", {"cunning": 48, "practice": 17}, 43, 65999),
        ShopItem("chest", 13, "SWитер от Мамы", {"cunning": 53, "practice": 19}, 46, 84999),
        ShopItem("chest", 14, "Сердце Джарвиса", {"cunning": 58, "practice": 21}, 49, 92349),
    ),
    "torso": (
        ShopItem("torso", 1, "Свитер с оленями", {"wisdom": 3}, 20, 29),
        ShopItem("torso", 2, "Жилетка Вассермана", {"wisdom": 6}, 21, 269),
        ShopItem("torso", 3, "Потрёпанный жилет", {"wisdom": 9}, 22, 629),
        ShopItem("torso", 4, "Обычный пиджак", {"wisdom": 12}, 23, 1139),
        ShopItem("torso", 5, 'Костюм "тройка"', {"wisdom": 15}, 24, 1949),
        ShopItem("torso", 6, "Костюм Berlini", {"wisdom": 19, "theory": 5}, 25, 3999),
        ShopItem("torso", 7, "Розовый кевлар", {"wisdom": 23, "theory": 7}, 28, 6449),
        ShopItem("torso", 8, "Телепузо", {"wisdom": 28, "theory": 9}, 31, 9999),
        ShopItem("torso", 9, "Куртка-X", {"cunning": 33, "practice": 11}, 34, 14999),
        ShopItem("torso", 10, "Контур человека", {"wisdom": 38, "theory": 13}, 37, 21674),
        ShopItem("torso", 11, "S-витшот", {"wisdom": 43, "theory": 15}, 40, 47999),
        ShopItem("torso", 12, "πджак", {"wisdom": 48, "theory": 17}, 43, 65999),
        ShopItem("torso", 13, "Форма мешка", {"wisdom": 53, "theory": 19}, 46, 84999),
        ShopItem("torso", 14, "Ягодный o-shhirt", {"wisdom": 58, "theory": 21}, 49, 92349),
    ),
}


_CODE = re.compile(r"([a-z])([0-9]+)")


def shop_item(code: str) -> ShopItem | None:
    m = _CODE.fullmatch(code)
    if m is None:
        return None
    slot = slot_of_letter(m.group(1))
    if slot is None or slot.shop is None:
        return None
    items = SHOP[slot.shop]
    tier = int(m.group(2))
    return items[tier - 1] if 1 <= tier <= len(items) else None


@dataclass(frozen=True, slots=True)
class SetItem:
    name: str
    level: int
    bonuses: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class CraftedSet:
    key: SetKey
    title: str
    line: str | None
    rank: int
    items: Mapping[UpSlot, tuple[SetItem, ...]]
    shop_tier: int | None


SET_SLOTS: tuple[UpSlot, ...] = ("book", "ring", "left", "right", "head", "legs", "chest", "torso")
BUYABLE_SETS: tuple[SetKey, ...] = ("summer", "autumn", "um", "pig")
SET_MIN_SLOTS = 7

_SET_ITEMS: dict[SetKey, dict[UpSlot, tuple[SetItem, ...]]] = {
    "summer": {
        "book": (SetItem("S-убноут", 35, {"theory": 15, "cunning": 15}),),
        "ring": (SetItem("S=πR^2", 35, {"practice": 15, "wisdom": 15}),),
        "left": (SetItem("S-ейчас", 38, {"theory": 29, "practice": 15}),),
        "right": (SetItem("S-март", 38, {"practice": 29, "theory": 15}),),
        "head": (SetItem("S-глаз", 39, {"theory": 27, "wisdom": 17, "practice": 10}),),
        "legs": (SetItem("S-ланцы", 39, {"practice": 27, "cunning": 17, "theory": 10}),),
        "chest": (SetItem("S-орочка", 40, {"cunning": 43, "practice": 15}),),
        "torso": (SetItem("S-витшот", 40, {"wisdom": 43, "theory": 15}),),
    },
    "autumn": {
        "book": (SetItem("MAC-адрес ноута", 39, {"theory": 20, "cunning": 20}),),
        "ring": (SetItem("Bat Ring", 39, {"practice": 20, "wisdom": 20}),),
        "left": (SetItem("U-Time", 41, {"theory": 33, "practice": 17}),),
        "right": (SetItem("Tullp Phone", 41, {"practice": 33, "theory": 17}),),
        "head": (SetItem("Линзы-Hooliнзы", 42, {"theory": 30, "wisdom": 19, "practice": 11}),),
        "legs": (SetItem("Rise of RoBoots", 42, {"practice": 30, "cunning": 19, "theory": 11}),),
        "chest": (SetItem("Arc Reactor", 43, {"cunning": 48, "practice": 17}),),
        "torso": (SetItem("πджак", 43, {"wisdom": 48, "theory": 17}),),
    },
    "um": {
        "book": (SetItem("WinPP", 42, {"theory": 25, "cunning": 25}),),
        "ring": (SetItem("Печать Ангела", 42, {"practice": 25, "wisdom": 25}),),
        "left": (SetItem("Um-Watch", 44, {"theory": 37, "practice": 19}),),
        "right": (SetItem("Um-Phone", 44, {"practice": 37, "theory": 19}),),
        "head": (SetItem("Концепт VRшлема", 45, {"theory": 33, "wisdom": 21, "practice": 12}),),
        "legs": (SetItem("Подкаблучники", 45, {"practice": 33, "cunning": 21, "theory": 12}),),
        "chest": (SetItem("SWитер от Мамы", 46, {"cunning": 53, "practice": 19}),),
        "torso": (SetItem("Форма мешка", 46, {"wisdom": 53, "theory": 19}),),
    },
    "pig": {
        "book": (SetItem("Певноут", 45, {"theory": 30, "cunning": 30}),),
        "ring": (SetItem("Моя прелесть", 45, {"practice": 30, "wisdom": 30}),),
        "left": (SetItem("Drink Time", 47, {"theory": 41, "practice": 21}),),
        "right": (SetItem("GiftPhone", 47, {"practice": 41, "theory": 21}),),
        "head": (SetItem("Головограмма", 48, {"theory": 36, "wisdom": 23, "practice": 13}),),
        "legs": (SetItem("Bat-Galoshi", 48, {"practice": 36, "cunning": 23, "theory": 13}),),
        "chest": (SetItem("Сердце Джарвиса", 49, {"cunning": 58, "practice": 21}),),
        "torso": (SetItem("Ягодный o-shhirt", 49, {"wisdom": 58, "theory": 21}),),
    },
    "y2020": {
        "book": (SetItem("БлокНотик", 45, {"theory": 35, "cunning": 35}),),
        "ring": (SetItem("Ring of PPower", 45, {"practice": 35, "wisdom": 35}),),
        "left": (SetItem("Chtozatime", 47, {"theory": 45, "practice": 23}),),
        "right": (SetItem("СоVirusPhone", 47, {"practice": 45, "theory": 23}),),
        "head": (SetItem("ХулятОчки", 48, {"theory": 39, "wisdom": 25, "practice": 14}),),
        "legs": (SetItem("Лапки в тапке", 48, {"practice": 39, "cunning": 25, "theory": 14}),),
        "chest": (SetItem("Jill’етка", 49, {"cunning": 63, "practice": 23}),),
        "torso": (SetItem("ДождеWeak", 49, {"wisdom": 63, "theory": 23}),),
    },
    "spring": {
        "book": (SetItem("WhyNote", 48, {"theory": 40, "cunning": 40}),),
        "ring": (SetItem("Ring of NaN", 48, {"practice": 40, "wisdom": 40}),),
        "left": (SetItem("TimeToWin", 50, {"theory": 49, "practice": 25}),),
        "right": (SetItem("Пепейджер", 50, {"practice": 49, "theory": 25}),),
        "head": (SetItem("Робоглазки", 51, {"theory": 42, "wisdom": 27, "practice": 15}),),
        "legs": (SetItem("NaNoBoots", 51, {"practice": 42, "cunning": 27, "theory": 15}),),
        "chest": (SetItem("AlwaysInTop", 52, {"cunning": 68, "practice": 25}),),
        "torso": (SetItem("WayneStyle", 52, {"wisdom": 68, "theory": 25}),),
    },
    "logistic": {
        "book": (
            SetItem("Е-нотик", 51, {"theory": 45, "cunning": 45}),
            SetItem("WeBook", 54, {"theory": 50, "cunning": 50}),
        ),
        "ring": (
            SetItem("Кольцо Теней", 51, {"practice": 45, "wisdom": 45}),
            SetItem("RedRing", 54, {"practice": 50, "wisdom": 50}),
        ),
        "left": (
            SetItem("ItsTimeToStop", 53, {"theory": 53, "practice": 27}),
            SetItem("SM-art", 56, {"theory": 57, "practice": 29}),
        ),
        "right": (
            SetItem("Чёртов смартфон", 53, {"practice": 53, "theory": 27}),
            SetItem("iBlackM", 56, {"practice": 57, "theory": 29}),
        ),
        "head": (
            SetItem("Толпызики", 54, {"theory": 45, "wisdom": 29, "practice": 16}),
            SetItem("Хиджаб", 57, {"theory": 48, "wisdom": 31, "practice": 17}),
        ),
        "legs": (
            SetItem("Мезозавры", 54, {"practice": 45, "cunning": 29, "theory": 16}),
            SetItem("Хулитопы", 57, {"practice": 48, "cunning": 31, "theory": 17}),
        ),
        "chest": (
            SetItem("RedQueenTop", 55, {"cunning": 73, "practice": 27}),
            SetItem("M-Zhilетka", 58, {"cunning": 78, "practice": 29}),
        ),
        "torso": (
            SetItem("ТелоGRAYка", 55, {"wisdom": 73, "theory": 27}),
            SetItem("PA’ltishCo", 58, {"wisdom": 78, "theory": 29}),
        ),
    },
}

# ключ, название, строка в /inv (у Um-сета не снята), тир магазина
_SET_META: tuple[tuple[SetKey, str, str | None, int | None], ...] = (
    ("summer", "🌞 Летний", "🌞Сет Летний", 11),
    ("autumn", "🍂 Осень", "🍂Сет Осень", 12),
    ("um", "Um-сет", None, 13),
    ("pig", "🐷 Свинтус", "🐷Сет Свинтус", 14),
    ("y2020", "🆘 2020", "🆘Сет 2020", None),
    ("spring", "🌸 Весенний", "🌸Сет Весенний", None),
    ("logistic", "🗳 Логистик", "🗳Сет Логистик", None),
)

SETS: dict[SetKey, CraftedSet] = {
    key: CraftedSet(key, title, line, rank, _SET_ITEMS[key], shop_tier)
    for rank, (key, title, line, shop_tier) in enumerate(_SET_META, start=1)
}

_SET_BY_NAME: dict[str, tuple[CraftedSet, UpSlot]] = {
    item.name.casefold(): (s, slot)
    for s in SETS.values()
    for slot, items in s.items.items()
    for item in items
}


def set_by_name(name: str) -> tuple[CraftedSet, UpSlot] | None:
    return _SET_BY_NAME.get(name.casefold())


_SET_BY_TIER = {s.shop_tier: s for s in SETS.values() if s.shop_tier is not None}


def set_part(icon: str, name: str, code: str | None) -> CraftedSet | None:
    """Сет гаджета на слоте значка `icon`: по названию, а магазинной части сета — и по коду
    (`/inv` может назвать её иначе, чем витрина)."""
    info = slot_of_icon(icon)
    if info is None:
        return None
    found = set_by_name(name)
    if found is not None and found[1] == info.up:
        return found[0]
    item = None if code is None else shop_item(code)
    if item is None or item.slot != info.shop:
        return None
    return _SET_BY_TIER.get(item.tier)


UPGRADE_KINDS: dict[UpgradeKind, tuple[str, str]] = {
    "white": ("⚪️", "low"),
    "blue": ("🔵", "middle"),
    "red": ("🔴", "high"),
}
KIND_BY_CALLBACK: dict[str, UpgradeKind] = {cb: kind for kind, (_, cb) in UPGRADE_KINDS.items()}
KIND_BY_ICON: dict[str, UpgradeKind] = {
    variant: kind
    for kind, (icon, _) in UPGRADE_KINDS.items()
    for variant in (icon, icon.replace(_VS16, ""))
}
