"""Гаджеты в состоянии: рюкзак и коды с `/inv`, покупка, апгрейды с `/upgrades` и `/up_`, серия
правок заточки (живая съёмка 06.10)."""

from typing import Any

from app.engine.bus import Bus
from app.engine.memory import MemoryJournal
from app.engine.parsing import default_parser
from app.engine.pipeline import Pipeline
from app.engine.settings import ChatsSection
from app.engine.state.ledger import Effect
from app.engine.state.model import CharacterState, GadgetsState, Obs, dump_state
from app.engine.state.reducer import StateReducer
from tests.engine.gadget_texts import (
    BOUGHT_RIGHT1,
    FAIL_0,
    GAME,
    INV,
    INV_BOUGHT,
    INV_UPGRADED_P1,
    INV_WORN_P1,
    OK_1,
    OK_2,
    UCON,
    UP_BUTTONS,
    UP_BUTTONS_AUTO,
    UP_RIGHT_0,
    UP_RIGHT_2,
    UPGRADES_CONFIRM_OFF,
    UPGRADES_CONFIRM_ON,
    UPGRADES_P1,
    WEAR_P1,
    game_edit,
    game_text,
)
from tests.engine.state.helpers import PARSER, at, value

State = dict[str, Any]
UP_MSG = 2


def apply(
    reducer: StateReducer,
    state: State,
    text: str,
    minutes: float,
    *,
    msg_id: int = 1,
    buttons: tuple[Any, ...] = (),
) -> State:
    msg = game_text(text, buttons=buttons, at=at(minutes), msg_id=msg_id)
    return reducer.apply(state, msg, PARSER.parse(msg))


def edit(
    reducer: StateReducer,
    state: State,
    text: str,
    minutes: float,
    *,
    msg_id: int,
    revision: int,
    created: float,
) -> tuple[State, tuple[Effect, ...]]:
    msg = game_edit(
        text,
        buttons=UP_BUTTONS,
        msg_id=msg_id,
        revision=revision,
        created=at(created),
        at=at(minutes),
    )
    new, effects = reducer.reduce(state, msg, PARSER.parse(msg))
    return new, tuple(effects)


def profile_money(n: int) -> State:
    return dump_state(CharacterState(money=Obs(value=n, at=at(0))))


def effects(text: str) -> list[Effect]:
    msg = game_text(text, at=at(0))
    return list(StateReducer().reduce({}, msg, PARSER.parse(msg))[1])


def gadget(state: State, name: str) -> dict[str, Any]:
    return next(i for i in value(state, "gadgets")["items"] if i["name"] == name)


def level_of(state: State, name: str) -> int | None:
    level: int | None = gadget(state, name)["level"]
    return level


def up_right(reducer: StateReducer, state: State, minutes: float = 0) -> State:
    return apply(reducer, state, UP_RIGHT_0, minutes, msg_id=UP_MSG, buttons=UP_BUTTONS_AUTO)


def test_inv_bag_and_codes() -> None:
    s = apply(StateReducer(), {}, INV_BOUGHT, 0)
    g = GadgetsState.model_validate(value(s, "gadgets"))
    assert g.bag[-1].code == "p1" and g.bag[-1].index == 11 and value(s, "bag") == 12
    assert len(g.bag) == 11 and g.bag[0].code == "t501" and g.bag[0].index == 1
    assert g.items[0].code == "h18" and g.items[0].index is None


def test_wear_answer_keeps_bag_counter_doubtful() -> None:
    r = StateReducer()
    s = apply(r, apply(r, {}, INV_BOUGHT, 0), WEAR_P1, 1, msg_id=2)
    assert s["bag"]["src"] == "doubtful" and value(s, "bag") == 12
    assert value(s, "gadgets")["sets"] == ["🗳Сет Логистик"]
    # Список рюкзака в ответе — уже после смены: снятый iBlackM на месте надетой мобилы.
    assert value(s, "gadgets")["bag"][-1]["code"] == "p18"
    assert s["gadgets"]["src"] == "screen" and value(s, "bag_cap") == 24


def test_bought_spends_catalog_price_and_doubts_gadgets() -> None:
    r = StateReducer()
    s = apply(r, profile_money(555), INV, 0)
    assert value(s, "bag") == 11
    s = apply(r, s, BOUGHT_RIGHT1, 1, msg_id=2)
    assert value(s, "money") == 552 and value(s, "bag") == 12 and s["gadgets"]["src"] == "doubtful"
    assert effects(BOUGHT_RIGHT1) == [Effect("gadget_buy", {"money": -3}, {"Китайская мобила": 1})]


def test_bought_unknown_item_doubts_money_without_effect() -> None:
    r = StateReducer()
    unknown = BOUGHT_RIGHT1.replace("Китайская мобила", "Мобила-Z")
    s = apply(r, profile_money(555), INV, 0)
    s = apply(r, s, unknown, 1, msg_id=2)
    assert s["money"] == {"value": 555, "at": "2026-09-26T09:00:00Z", "src": "doubtful"}
    assert value(s, "bag") == 12 and s["gadgets"]["src"] == "doubtful"
    assert effects(unknown) == []


def test_fresh_inv_after_purchase_clears_doubt() -> None:
    r = StateReducer()
    s = apply(r, profile_money(555), INV, 0)
    s = apply(r, s, BOUGHT_RIGHT1, 1, msg_id=2)
    s = apply(r, s, INV_BOUGHT, 2, msg_id=3)
    assert s["gadgets"]["src"] == "screen" and s["bag"]["src"] == "screen"
    assert value(s, "gadgets")["bag"][-1]["code"] == "p1"


def test_upgrades_screen_snaps_stocks_info_and_levels() -> None:
    r = StateReducer()
    s = apply(r, apply(r, {}, INV_WORN_P1, 0), UPGRADES_CONFIRM_ON, 1, msg_id=2)
    assert value(s, "upgrades") == {"white": 10335, "blue": 4840, "red": 2532}
    assert value(s, "upgrade_info") == {
        "chances": {"white": 65, "blue": 75, "red": 85},
        "upgrademan_pct": 5,
        "confirm": True,
    }
    right = gadget(s, "Китайская мобила")
    assert (right["grade"], right["level"]) == ("⚪️", 3)
    # Уровни обновлены, а момент снимка списка — прежний: рюкзак с экрана апгрейдов не виден.
    assert (s["gadgets"]["at"], s["gadgets"]["src"]) == ("2026-09-26T09:00:00Z", "screen")
    assert value(s, "gadgets")["sets"] == ["🗳Сет Логистик"]


def test_upgrade_screen_snaps_stocks_and_level_without_moving_snapshot() -> None:
    r = StateReducer()
    s = apply(r, apply(r, {}, INV_WORN_P1, 0), UP_RIGHT_2, 1, msg_id=2, buttons=UP_BUTTONS_AUTO)
    assert value(s, "upgrades") == {"white": 10336, "blue": 4840, "red": 2532}
    assert level_of(s, "Китайская мобила") == 2
    assert s["gadgets"]["at"] == "2026-09-26T09:00:00Z"
    assert value(s, "upgrade_info") is None


def test_upgrades_screen_with_other_gadget_on_slot_doubts_list() -> None:
    r = StateReducer()
    # На /inv 02 в правой руке iBlackM, на экране апгрейдов — уже мобила.
    s = apply(r, apply(r, {}, INV, 0), UPGRADES_P1, 1, msg_id=2)
    assert s["gadgets"]["src"] == "doubtful"
    assert gadget(s, "iBlackM")["level"] == 25


def _series(r: StateReducer) -> tuple[State, list[Effect]]:
    s = up_right(r, apply(r, {}, INV_WORN_P1, 0))
    assert value(s, "upgrades")["white"] == 10340
    out: list[Effect] = []
    for rev, text in enumerate((FAIL_0, OK_1, OK_2), start=1):
        s, found = edit(r, s, text, rev, msg_id=UP_MSG, revision=rev, created=0)
        out.extend(found)
    return s, out


def test_three_edits_of_one_message_spend_three() -> None:
    s, all_effects = _series(StateReducer())
    assert value(s, "upgrades")["white"] == 10337 and s["upgrades"]["src"] == "derived"
    assert [e.kind for e in all_effects] == ["gadget_upgrade"] * 3
    assert [e.amounts for e in all_effects] == [{"upgrades_white": -1}] * 3
    assert [e.items for e in all_effects] == [
        {"up:right": 1, "fail": 1},
        {"up:right": 1, "ok": 1},
        {"up:right": 1, "ok": 1},
    ]
    assert level_of(s, "Китайская мобила") == 2
    assert gadget(s, "Китайская мобила")["grade"] == "⚪️"
    assert (s["gadgets"]["at"], s["gadgets"]["src"]) == ("2026-09-26T09:00:00Z", "screen")


def test_same_revision_again_changes_nothing() -> None:
    r = StateReducer()
    s, _ = _series(r)
    again, found = edit(r, s, OK_2, 3, msg_id=UP_MSG, revision=3, created=0)
    assert again == s and found == ()
    # Ключ итога — один на сообщение: ключи прежних ревизий не копятся.
    keys = [k for k in s["applied"] if k.startswith(f"{GAME}:{UP_MSG}:")]
    assert keys == [f"{GAME}:{UP_MSG}:upgrade_attempt:3"]


def test_edit_of_same_second_as_snapshot_doubts() -> None:
    r = StateReducer()
    s = up_right(r, apply(r, {}, INV_WORN_P1, 0))
    s = apply(r, s, UPGRADES_P1, 1, msg_id=3)
    s, found = edit(r, s, FAIL_0, 1, msg_id=UP_MSG, revision=1, created=0)
    assert s["upgrades"]["src"] == "doubtful" and value(s, "upgrades")["white"] == 10340
    assert [e.kind for e in found] == ["gadget_upgrade"]
    # Следующая правка — уже после снимка: списание снова известно (сомнение остаётся).
    s, _ = edit(r, s, OK_1, 2, msg_id=UP_MSG, revision=2, created=0)
    assert value(s, "upgrades")["white"] == 10339 and s["upgrades"]["src"] == "doubtful"


def test_attempt_older_than_gadgets_snapshot_keeps_level() -> None:
    r = StateReducer()
    s = up_right(r, apply(r, {}, INV_WORN_P1, 0))
    s = apply(r, s, INV_UPGRADED_P1, 5, msg_id=3)
    s, found = edit(r, s, OK_1, 2, msg_id=UP_MSG, revision=1, created=0)
    assert level_of(s, "Китайская мобила") == 3
    assert value(s, "upgrades")["white"] == 10339
    assert [e.items for e in found] == [{"up:right": 1, "ok": 1}]


def test_attempt_for_other_gadget_on_slot_doubts_list() -> None:
    r = StateReducer()
    s = up_right(r, apply(r, {}, INV, 0))
    assert s["gadgets"]["src"] == "doubtful"
    s = apply(r, {}, INV, 0)
    s, _ = edit(r, s, OK_1, 1, msg_id=UP_MSG, revision=1, created=0)
    assert s["gadgets"]["src"] == "doubtful" and gadget(s, "iBlackM")["level"] == 25


def test_confirm_set_updates_known_info_only() -> None:
    r = StateReducer()
    s = apply(r, {}, UCON, 0)
    assert value(s, "upgrade_info") is None
    s = apply(r, s, UPGRADES_CONFIRM_OFF, 1, msg_id=2)
    assert value(s, "upgrade_info")["confirm"] is False
    s = apply(r, s, UCON, 2, msg_id=3)
    assert value(s, "upgrade_info") == {
        "chances": {"white": 65, "blue": 75, "red": 85},
        "upgrademan_pct": 5,
        "confirm": True,
    }


async def test_pipeline_applies_every_revision_once() -> None:
    journal = MemoryJournal()
    pipe = Pipeline(
        journal=journal,
        parser=default_parser(ChatsSection()),
        reducer=StateReducer(),
        bus=Bus(),
        retry_base_s=0.01,
    )
    await pipe.process(game_text(UP_RIGHT_0, buttons=UP_BUTTONS_AUTO, at=at(0), msg_id=UP_MSG))
    edits = [
        game_edit(text, buttons=UP_BUTTONS, msg_id=UP_MSG, revision=rev, created=at(0), at=at(rev))
        for rev, text in enumerate((FAIL_0, OK_1, OK_2), start=1)
    ]
    for msg in edits:
        assert await pipe.process(msg) is not None
    # Повтор последней правки (сверка истории, переподключение) — уже в журнале.
    assert await pipe.process(edits[-1]) is None
    rows = [(m.revision, e.items) for m, e, _ in journal.ledger if e.kind == "gadget_upgrade"]
    assert rows == [
        (1, {"up:right": 1, "fail": 1}),
        (2, {"up:right": 1, "ok": 1}),
        (3, {"up:right": 1, "ok": 1}),
    ]
    assert value(pipe.state, "upgrades")["white"] == 10337
