from dataclasses import replace

from app.engine.state.reducer import StateReducer
from tests.engine.state.helpers import PARSER, at, feed, fixture_at, value
from tests.fixtures import game_versions

PROFILE = 3624478


def _profiled(reducer: StateReducer) -> dict:
    return feed(reducer, {}, "profile", PROFILE, 0)


def test_forced_sleep_and_wake_up() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "sleep", 3517441, 1)
    assert value(state, "busy") == {"activity": "sleep_bridge", "until": "2026-09-26T21:01:00Z"}
    assert value(state, "sleep_deadline") is None
    state = feed(reducer, state, "sleep", 3517730, 720)
    assert value(state, "busy") is None
    assert value(state, "woke_at") == "2026-09-26T21:00:00Z"
    assert value(state, "sleep_deadline") == "2026-09-29T21:00:00Z"
    assert value(state, "sleep_allowed_at") == "2026-09-27T09:00:00Z"
    assert (value(state, "stamina"), value(state, "exp")) == (100, 17496049 + 135)


def test_hotel_sleep_costs_money_and_price_known() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "sleep", 3526861, 1)
    assert state["prices"]["hotel"]["value"]["money"] == 210
    state = feed(reducer, state, "sleep", 3525189, 2)
    assert value(state, "money") == 867 - 210
    assert value(state, "busy")["activity"] == "sleep_hotel"


def test_live_hotel_sleep_charged_once() -> None:
    """Отель списывает итог «Ты отправился спать…»; отдельное «Ты потратился на отель…» (оно
    пришло раньше правки) деньги не трогает."""
    reducer = StateReducer()
    state = _profiled(reducer)
    for n, minute in ((0, 1), (1, 1.1)):
        msg = replace(game_versions("sleep", 3625590)[n], date=at(minute), created_at=at(1))
        state = reducer.apply(state, msg, PARSER.parse(msg))
    assert state["prices"]["hotel"]["value"]["money"] == 213
    ack = fixture_at("sleep", 3625591, 1.3)
    state = reducer.apply(state, ack, PARSER.parse(ack))
    assert value(state, "money") == 867
    asleep = replace(game_versions("sleep", 3625590)[2], date=at(1.3), created_at=at(1))
    state = reducer.apply(state, asleep, PARSER.parse(asleep))
    again = reducer.apply(state, asleep, PARSER.parse(asleep))
    assert value(again, "money") == 867 - 213
    assert value(again, "busy")["activity"] == "sleep_hotel"


def test_robbery_fight_wakes_up() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "sleep", 3517441, 1)
    state = feed(reducer, state, "sleep", 3520076, 30)
    assert value(state, "busy") is None
    assert (value(state, "money"), value(state, "stamina")) == (863, 0)


def test_robbed_asleep_loses_money_once_and_keeps_sleeping() -> None:
    # Не проснулся: итог — отдельное сообщение, потеря в нём — точной суммой.
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "sleep", 3517441, 1)
    state = feed(reducer, state, "sleep", 3420239, 30)
    again = feed(reducer, state, "sleep", 3420239, 30)
    for s in (state, again):
        assert value(s, "busy")["activity"] == "sleep_bridge"
        assert (value(s, "money"), value(s, "exp")) == (867 - 38, 17496049 + 118)
        assert s["money"]["src"] == "derived"


def test_robbed_without_money_line_doubts_money() -> None:
    reducer = StateReducer()
    state = _profiled(reducer)
    msg = fixture_at("sleep", 3420239, 30)
    text = (msg.text or "").split("\n\nТы потерял:")[0]
    lost = replace(msg, text=text)
    state = reducer.apply(state, lost, PARSER.parse(lost))
    assert (value(state, "money"), state["money"]["src"]) == (867, "doubtful")
    assert value(state, "exp") == 17496049 + 118


def test_robbed_with_zero_money_line_is_not_doubtful() -> None:
    # «-$0» — строка потери есть: сумма известна точно, деньги не помечаются недостоверными.
    reducer = StateReducer()
    state = _profiled(reducer)
    msg = fixture_at("sleep", 3420239, 30)
    text = (msg.text or "").replace("-$38", "-$0")
    lost = replace(msg, text=text)
    state = reducer.apply(state, lost, PARSER.parse(lost))
    assert (value(state, "money"), state["money"]["src"]) == (867, "screen")
    assert value(state, "exp") == 17496049 + 118


def test_sleep_warning_sets_deadline() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "sleep", 3517243, 1)
    assert value(state, "sleep_deadline") == "2026-09-26T11:01:00Z"


def test_deeds_menu_and_prices() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3548692, 1)
    assert value(state, "busy") == {"activity": "sleep_hotel", "until": "2026-09-26T16:00:00Z"}
    assert state["prices"]["rob"]["value"] == {
        "motivation": 1,
        "money": 0,
        "minutes": 8,
        "details": 0,
        "white": 0,
        "blue": 0,
        "knowledge": 0,
    }
    state = feed(reducer, state, "activities", 3624645, 2)
    assert state["prices"]["learn"]["value"]["motivation"] == 2
    state = feed(reducer, state, "activities", 3603614, 3)
    assert value(state, "motivation") == 70


def test_workshop_snapshot_and_upgrades_from_rewards() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3624750, 1)
    assert value(state, "upgrades") == {"white": 10243, "blue": 4797, "red": 2525}
    assert (value(state, "money"), value(state, "details")) == (862, 136661)
    state = feed(reducer, state, "gorbushka", 3516744, 2)
    assert value(state, "upgrades") == {"white": 10243, "blue": 4798, "red": 2525}
    assert (value(state, "prizebox"), value(state, "prizebox_ready_at")) == (True, None)


def test_workshop_upgrade_same_second_marks_doubtful() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3624750, 1)
    same_second = feed(reducer, state, "gorbushka", 3516744, 1, created=1)
    assert value(same_second, "upgrades") == {"white": 10243, "blue": 4797, "red": 2525}
    assert same_second["upgrades"]["src"] == "doubtful"


def test_food_menu_and_fastfood() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "food", 3624997, 1)
    assert value(state, "stamina") == 52
    assert value(state, "fastfood_ready_at") == "2026-09-26T09:30:00Z"
    assert value(state, "food_stock")["banana"] == {"count": 12, "low": 150, "high": 275}
    state = feed(reducer, state, "food", 3536881, 31)
    assert (value(state, "stamina"), value(state, "motivation")) == (226, 73)
    assert value(state, "food_stock")["banana"]["count"] == 11
    assert value(state, "fastfood_ready_at") == "2026-09-26T10:01:00Z"


def test_food_menu_and_fastfood_same_second_marks_stock_doubtful() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "food", 3624997, 1)
    same_second = feed(reducer, state, "food", 3536881, 1, created=1)
    assert value(same_second, "food_stock")["banana"]["count"] == 12
    assert same_second["food_stock"]["src"] == "doubtful"


def test_inventory_books_cards() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "items", 3625102, 1)
    assert (value(state, "books"), value(state, "cards"), value(state, "prizebox")) == (
        875,
        13,
        True,
    )
    assert value(state, "prizebox_ready_at") == "2026-09-27T01:49:00Z"
    state = feed(reducer, state, "items", 3516680, 2)
    assert (value(state, "books"), value(state, "exp")) == (874, 17496049 + 457)
    assert value(state, "book_ready_at") == "2026-09-26T09:52:00Z"
    state = feed(reducer, state, "items", 3516678, 3)
    assert (value(state, "cards"), value(state, "money")) == (12, 867 + 597)
    assert value(state, "card_ready_at") == "2026-09-26T09:53:00Z"


def test_inventory_timers_and_missing_cards_line() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "items", 3618363, 1)
    assert (value(state, "books"), value(state, "cards")) == (848, 15)
    assert value(state, "book_ready_at") == "2026-09-26T09:39:00Z"
    assert value(state, "card_ready_at") == "2026-09-26T09:39:00Z"
    state = feed(reducer, state, "items", 3595611, 2)
    assert (value(state, "books"), value(state, "cards")) == (785, 0)
    assert value(state, "book_ready_at") == "2026-09-26T09:02:00Z"
    assert value(state, "card_ready_at") == "2026-09-26T09:02:00Z"
    assert value(state, "prizebox_ready_at") == "2026-09-27T00:08:00Z"


def test_gifts_containers_prizebox() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "items", 3623585, 1)
    assert value(state, "containers_small") == 5
    state = feed(reducer, state, "items", 3517971, 2)
    assert value(state, "containers_small") == 4
    state = feed(reducer, state, "items", 3517262, 3)
    assert (value(state, "prizebox"), value(state, "money")) == (False, 1108)


def test_container_and_prizebox_contents_applied() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "items", 3623585, 1)
    state = feed(reducer, state, "items", 3517971, 2)
    # Малый: 🔩 +2 (улучшения до экрана мастерской неизвестны — не трогаются).
    assert (value(state, "raw"), value(state, "containers_small")) == (21310 + 2, 4)
    state = feed(reducer, state, "items", 3611233, 3)
    assert value(state, "details") == 136671 + 6
    state = feed(reducer, state, "items", 3625717, 4)
    assert value(state, "exp") == 17496049 + 165
    # Деньги коробки — снимок «Стало», явная прибавка их не задваивает.
    state = feed(reducer, state, "items", 3517262, 5)
    assert value(state, "money") == 1108


def test_logistic_container_from_harvest_counted() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "items", 3623585, 1)
    state = feed(reducer, state, "activities", 3610665, 2)
    assert value(state, "containers_small") == 6
    state = feed(reducer, state, "activities", 3609456, 3)
    assert value(state, "containers_medium") == 1


def test_gorbushka_flow() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "gorbushka", 3516738, 1)
    assert value(state, "gorbushka") == {
        "state": "meeting",
        "won": 0,
        "total": 4,
        "ticket_until": "2026-09-27T09:00:00Z",
        "next_fight_at": "2026-09-26T09:01:00Z",
        "comeback_at": None,
        "fight_cost": 1,
    }
    state = feed(reducer, state, "gorbushka", 3516739, 2)
    gorb = value(state, "gorbushka")
    assert (gorb["state"], gorb["won"], gorb["next_fight_at"]) == (
        "waiting",
        1,
        "2026-09-26T10:02:00Z",
    )
    assert value(state, "exp") == 17496049 + 224
    assert value(state, "motivation") == 72 - 1
    need = feed(reducer, _profiled(reducer), "gorbushka", 3520526, 1)
    assert (value(need, "money"), value(need, "knowledge")) == (104, 17627)
    assert need["prices"]["gorbushka_ticket"]["value"]["knowledge"] == 20
    # Дневной лимит продаванов — с экрана билета.
    assert value(need, "gorbushka")["total"] == 4


def test_gorbushka_ticket_purchase_deducted() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "gorbushka", 3537930, 1)
    assert (value(state, "money"), value(state, "knowledge")) == (1544, 17977)
    # Покупка — правка того же сообщения: снимок ресурсов мог её учесть.
    same_message = feed(reducer, state, "gorbushka", 3516738, 2, created=1)
    assert same_message["money"]["src"] == "doubtful"
    assert value(same_message, "gorbushka")["state"] == "meeting"
    # Если снимок ресурсов старше сообщения, цена вычитается.
    later = feed(reducer, state, "gorbushka", 3516738, 3, created=2)
    assert (value(later, "money"), value(later, "knowledge")) == (1544 - 120, 17977 - 20)


def test_gorbushka_ticket_bought_before_newer_snapshot_marks_doubtful() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "gorbushka", 3537930, 1)
    # Профиль после покупки билета уже учёл её: второй раз вычитать нельзя.
    state = feed(reducer, state, "profile", PROFILE, 1.5)
    state = feed(reducer, state, "gorbushka", 3516738, 2, created=2)
    assert value(state, "gorbushka")["state"] == "meeting"
    assert (value(state, "money"), value(state, "knowledge")) == (867, 21942)
    assert (state["money"]["src"], state["knowledge"]["src"]) == ("doubtful", "doubtful")


def test_motivation_full_clears_timer() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3517795, 1)
    assert value(state, "motivation") == 85
    assert value(state, "motivation_next_at") is None
    assert state["motivation_next_at"]["src"] == "derived"


def test_levelup_steps() -> None:
    reducer = StateReducer()
    profiled = _profiled(reducer)
    skills = value(profiled, "skills")
    state = feed(reducer, profiled, "levelup", 3532816, 1)
    assert value(state, "levelup_pending") is True
    state = feed(reducer, state, "levelup", 3532818, 2)
    skills = {**skills, "practice": skills["practice"] + 1}
    assert value(state, "skills") == skills
    state = feed(reducer, state, "levelup", 3532820, 3)
    assert value(state, "levelup_pending") is False
    assert (value(state, "money"), value(state, "motivation")) == (867 + 142, 73)
    assert value(state, "skills") == {**skills, "cunning": skills["cunning"] + 1}
    assert state["skills"]["src"] == "derived"


def test_levelup_skill_needs_known_skills() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "levelup", 3532818, 1)
    assert value(state, "skills") is None
