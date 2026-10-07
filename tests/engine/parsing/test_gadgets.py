from datetime import UTC, datetime

import pytest

from app.engine.events import Event, Unrecognized
from app.engine.parsing import default_parser
from app.engine.parsing.gadgets import (
    GadgetBought,
    GadgetUnworn,
    GadgetWorn,
    ShopOffer,
    ShopScreen,
    UpgradeAttempt,
    UpgradeConfirm,
    UpgradeConfirmSet,
    UpgradeDeclined,
    UpgradeScreen,
    UpgradesScreen,
)
from app.engine.parsing.items import Inventory
from app.engine.parsing.refusals import Refused
from app.engine.parsing.screens import InfoScreen
from app.engine.settings import ChatsSection
from app.engine.types import Button
from tests.engine import gadget_texts as texts
from tests.engine.gadget_texts import (
    BOUGHT_RIGHT1,
    CONFIRM_2,
    CONFIRM_3,
    CONFIRM_BUTTONS,
    DECLINED_3,
    FAIL_0,
    FAIL_0_DOT,
    INV_BOUGHT,
    NETWORK,
    NO_MONEY_RIGHT14,
    OK_1,
    OK_2,
    OK_3,
    SHOP_MENU,
    SHOP_RIGHT,
    UCOFF,
    UCON,
    UNWEAR_P1,
    UP_BUTTONS,
    UP_BUTTONS_AUTO,
    UP_LEFT_BUTTONS,
    UP_LEFT_PROD,
    UP_RIGHT_2,
    UPGRADES,
    UPGRADES_CONFIRM_OFF,
    UPGRADES_CONFIRM_ON,
    WEAR_P1,
    WEAR_P18,
    game_edit,
    game_text,
)

PARSER = default_parser(ChatsSection())
NOW = datetime(2026, 10, 6, 14, 40, tzinfo=UTC)


def events(text: str, *, buttons: tuple[Button, ...] = (), edit: bool = False) -> list[Event]:
    if edit:
        msg = game_edit(text, buttons=buttons, msg_id=7, revision=5, created=NOW, at=NOW)
    else:
        msg = game_text(text, buttons=buttons)
    return PARSER.parse(msg)


def only[E: Event](
    cls: type[E], text: str, *, buttons: tuple[Button, ...] = (), edit: bool = False
) -> E:
    found = [e for e in events(text, buttons=buttons, edit=edit) if isinstance(e, cls)]
    assert len(found) == 1, found
    return found[0]


def test_showcase_right() -> None:
    e = only(ShopScreen, SHOP_RIGHT)
    assert e.slot == "right" and e.money == 555 and len(e.offers) == 14
    assert e.offers[6] == ShopOffer(
        tier=7, name="Чертёж телефона", bonuses={"practice": 13, "theory": 5}, level=26, price=2519
    )
    assert e.offers[0].level is None


def test_shop_menu_and_network_are_info() -> None:
    assert events(SHOP_MENU) == [InfoScreen(name="shop")]
    assert events(NETWORK) == [InfoScreen(name="network")]


def test_bought() -> None:
    assert events(BOUGHT_RIGHT1) == [
        GadgetBought(name="Китайская мобила", bonuses={"practice": 1})
    ]
    assert GadgetBought.outcome


def test_no_money_refusal() -> None:
    assert events(NO_MONEY_RIGHT14) == [Refused(reason="gadget_no_money", need=59444)]


def test_inv_reads_bag_with_codes() -> None:
    inv = only(Inventory, INV_BOUGHT)
    assert inv.gadgets.items[4].code == "p18" and not inv.after_change
    last = inv.gadgets.bag[-1]
    assert (last.index, last.code, last.grade, last.name) == (11, "p1", None, "Китайская мобила")
    assert inv.gadgets.sets == ("⚫️Сет VIP", "🗳Сет Логистик", "🗺Сет Кладоискатель", "🦉Сет Сова")


def test_wear_answer_has_sets_in_tail_and_worn_line() -> None:
    inv = only(Inventory, WEAR_P1)
    assert inv.after_change and inv.gadgets.sets == ("🗳Сет Логистик",)
    assert inv.gadgets.bag[-1].code == "p18" and inv.bag == 12
    assert only(GadgetWorn, WEAR_P1) == GadgetWorn(
        grade=None, level=None, name="Китайская мобила", bonuses={"practice": 1}, mark=None
    )


def test_wear_back_restores_sets() -> None:
    worn = only(GadgetWorn, WEAR_P18)
    assert (worn.grade, worn.level, worn.name, worn.mark) == ("⚫️", 25, "iBlackM", "💎")
    assert len(only(Inventory, WEAR_P18).gadgets.sets) == 4


def test_unwear_answer() -> None:
    assert only(GadgetUnworn, UNWEAR_P1).level == 3
    assert only(Inventory, UNWEAR_P1).gadgets.bag[-1].code == "p1"
    assert only(Inventory, UNWEAR_P1).gadgets.sets == ("⚫️Сет VIP", "🗳Сет Логистик")


@pytest.mark.parametrize(
    ("text", "confirm"),
    [(UPGRADES, False), (UPGRADES_CONFIRM_ON, True), (UPGRADES_CONFIRM_OFF, False)],
)
def test_upgrades_screen(text: str, confirm: bool) -> None:
    e = only(UpgradesScreen, text)
    assert e.confirm is confirm and e.upgrademan_pct == 5
    assert e.chances == {"white": 65, "blue": 75, "red": 85}
    assert dict(e.items)["right"].name in ("iBlackM", "Китайская мобила")
    assert len(e.items) == 10


def test_upgrades_screen_stocks() -> None:
    assert only(UpgradesScreen, UPGRADES).stocks == {"white": 10340, "blue": 4840, "red": 2532}
    item = dict(only(UpgradesScreen, UPGRADES_CONFIRM_ON).items)["right"]
    assert (item.grade, item.level) == ("⚪️", 3)


def test_upgrades_screen_without_worn_gadgets() -> None:
    # Ничего не надето: строк гаджетов нет, запасы и шансы на месте.
    lines = UPGRADES.split("\n")
    bare = "\n".join(line for line in lines if "/up_" not in line)
    e = only(UpgradesScreen, bare)
    assert e.items == ()
    assert e.stocks == {"white": 10340, "blue": 4840, "red": 2532}
    # Без полного блока запасов — не этот экран.
    cut = bare.replace("🔴 уникальные: 2532\xa0шт. (85%)\n", "")
    assert [e for e in events(cut) if isinstance(e, UpgradesScreen)] == []


def test_up_screen() -> None:
    e = only(UpgradeScreen, UP_RIGHT_2, buttons=UP_BUTTONS_AUTO)
    assert (e.up_slot, e.grade, e.level, e.name) == ("right", "⚪️", 2, "Китайская мобила")
    assert e.stocks["white"] == 10336 and e.chances["red"] == 85


def test_up_screen_without_upgrade() -> None:
    e = only(UpgradeScreen, texts.UP_RIGHT_0, buttons=UP_BUTTONS_AUTO)
    assert (e.grade, e.level, e.name) == (None, None, "Китайская мобила")


def test_up_screen_two_skills_without_auto_button() -> None:
    e = only(UpgradeScreen, UP_LEFT_PROD, buttons=UP_LEFT_BUTTONS)
    assert (e.up_slot, e.grade, e.level, e.name) == ("left", None, None, "Chtozatime")
    assert e.stocks == {"white": 1492, "blue": 1047, "red": 272}
    assert e.chances == {"white": 65, "blue": 75, "red": 85}


def test_up_screen_three_skills() -> None:
    text = (
        "⚫️26\xa0🕶Хиджаб\n"
        "🎓\xa085 + 78% = 151.30\n"
        "🐢\xa055 + 78% = 97.90\n"
        "🔨\xa030 + 78% = 53.40\n\n" + UP_LEFT_PROD.split("\n\n", 1)[1].replace(" шт.", "\xa0шт.")
    )
    buttons = tuple(
        Button(b.text, b.row, b.col, (b.data or "").replace("left", "head"))
        for b in UP_LEFT_BUTTONS
    )
    e = only(UpgradeScreen, text, buttons=buttons)
    assert (e.up_slot, e.grade, e.level, e.name) == ("head", "⚫️", 26, "Хиджаб")
    assert e.stocks == {"white": 1492, "blue": 1047, "red": 272}


@pytest.mark.parametrize(
    ("text", "level", "success", "nxt"),
    [
        (FAIL_0, 0, False, (1, 0)),
        (FAIL_0_DOT, 0, False, (1, 0)),
        (OK_1, 1, True, (2, 1)),
        (OK_2, 2, True, (3, 2)),
        (OK_3, 3, True, (4, 3)),
    ],
)
def test_attempt(text: str, level: int, success: bool, nxt: tuple[int, int]) -> None:
    e = only(UpgradeAttempt, text, buttons=UP_BUTTONS, edit=True)
    assert (e.up_slot, e.used, e.level, e.success, (e.next_ok, e.next_fail)) == (
        "right",
        "white",
        level,
        success,
        nxt,
    )
    assert UpgradeAttempt.per_revision and UpgradeAttempt.outcome


def test_attempt_header_and_name() -> None:
    e = only(UpgradeAttempt, OK_2, buttons=UP_BUTTONS, edit=True)
    assert (e.grade, e.name) == ("⚪️", "Китайская мобила")
    assert only(UpgradeAttempt, FAIL_0, buttons=UP_BUTTONS, edit=True).grade is None


def test_attempt_needs_upgrade_buttons() -> None:
    assert not [e for e in events(OK_1, edit=True) if isinstance(e, UpgradeAttempt)]


def test_confirm_and_decline() -> None:
    assert only(UpgradeConfirm, CONFIRM_2, buttons=CONFIRM_BUTTONS, edit=True) == UpgradeConfirm(
        up_slot="right", name="Китайская мобила", grade="⚪️", level=2, upgrade="white", chance=65
    )
    assert only(UpgradeConfirm, CONFIRM_3, buttons=CONFIRM_BUTTONS, edit=True).chance == 65
    d = only(UpgradeDeclined, DECLINED_3, buttons=UP_BUTTONS, edit=True)
    assert (d.up_slot, d.level, d.name, d.grade) == ("right", 3, "Китайская мобила", "⚪️")


def test_confirm_mode_answers() -> None:
    assert events(UCON) == [UpgradeConfirmSet(on=True)]
    assert events(UCOFF) == [UpgradeConfirmSet(on=False)]


_BUTTONS = {
    "UP_RIGHT_0": UP_BUTTONS_AUTO,
    "UP_RIGHT_2": UP_BUTTONS_AUTO,
    "UP_LEFT_PROD": UP_LEFT_BUTTONS,
    "FAIL_0": UP_BUTTONS,
    "FAIL_0_DOT": UP_BUTTONS,
    "OK_1": UP_BUTTONS,
    "OK_2": UP_BUTTONS,
    "OK_3": UP_BUTTONS,
    "CONFIRM_2": CONFIRM_BUTTONS,
    "CONFIRM_3": CONFIRM_BUTTONS,
    "DECLINED_3": UP_BUTTONS,
}
_LIVE = [name for name in dir(texts) if name.isupper() and isinstance(getattr(texts, name), str)]


@pytest.mark.parametrize("name", [n for n in _LIVE if n != "UNKNOWN"])
def test_no_unrecognized_in_live_shoot(name: str) -> None:
    found = events(getattr(texts, name), buttons=_BUTTONS.get(name, ()), edit=name in _BUTTONS)
    assert found
    assert not [e for e in found if isinstance(e, Unrecognized)]


def test_unknown_command_answer_is_still_a_refusal() -> None:
    assert events(texts.UNKNOWN) == [Refused(reason="unknown_command")]
