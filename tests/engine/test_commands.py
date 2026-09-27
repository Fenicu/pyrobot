import pytest

from app.engine.commands import (
    CommandClass,
    classify_callback,
    classify_text,
    feature_of_callback,
    feature_of_text,
    spends_nothing_callback,
)

N, A, R, F, D = (
    CommandClass.NAV,
    CommandClass.ACTION,
    CommandClass.RISKY,
    CommandClass.FORBIDDEN,
    CommandClass.DONATE,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("😎Я", N),
        ("/full", N),
        ("⏳Дела", N),
        ("/stock", N),
        ("🏛Горбушка", N),
        ("/help_skills", N),
        ("/battle19", N),
        ("/lab3", N),
        ("/topjob", N),
        ("🚇Метро", N),
        ("⏳Задания", N),
        ("/t_jobMoney_hard", A),
        ("⛏Добывать", A),
        ("/harvest", A),
        ("📯Pied Piper", A),
        ("🛡Защита", A),
        ("/buys_stark_12", A),
        ("/sells_umbrl_3", A),
        ("🌭Хот-дог", A),
        ("/read_exp", A),
        ("/unbox_ls", A),
        ("join_fight_I16YW9RrvSq", A),
        ("/gt", A),
        ("⚙️ → 🔩", A),
        ("/buys_bmesa_5", R),
        ("/sells_bmesa_1", R),
        ("⚪️ → 🔵", R),
        ("+🍀🐀", R),
        ("🚕Тачка", R),
        ("/ucon", R),
        ("/changecompany", F),
        ("/profreset", F),
        ("/setfullprofile", F),
        ("/buy_right3", F),
        ("/sell_1_t501", F),
        ("/sells_all", F),
        ("/up_head", F),
        ("/wear_1_t501", F),
        ("🎯 Дартс", F),
        ("/main", F),
        ("/fullt", F),
        ("/finish", D),
        ("/donate", D),
        ("/co_premium", D),
        ("+🔵 редкие", D),
        ("какой-то текст", F),
        ("", F),
    ],
)
def test_classify_text(text: str, expected: CommandClass) -> None:
    assert classify_text(text) is expected


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("maze_up", A),
        ("maze_first_aid_accept", A),
        ("maze_nothing", N),
        ("gorbushka_new", A),
        ("gorbushka_fight", A),
        ("gorbushka_new_decline", N),
        ("sleep_7", A),
        ("sleep_Hotel", A),
        ("sleep_Bridge", A),
        ("sleep_13", F),
        ("sleep_Moon", F),
        ("sm_drop_3", A),
        ("smoothie_accept", A),
        ("buys_stark", A),
        ("buys_bmesa", R),
        ("sells_stark", R),
        ("cancel_inline", N),
        ("take_up_low_money_1", F),
        ("buy_mercenaries_knows1", F),
        ("fit_106", F),
        ("subprof_select_packRat_ragman", F),
        ("subprof_select_decline", N),
        ("pet_select_accept_dog", F),
        ("pet_feast_accept_mouse", A),
        ("t_jobMoney_hard_confirm", A),
        ("tasksel_decline", N),
        ("rob_awake_1106993", A),
        ("rob_awake_", F),
        ("rob_awake_12x", F),
        ("mether_buy_coins", D),
        ("unknown_cb", F),
    ],
)
def test_classify_callback(data: str, expected: CommandClass) -> None:
    assert classify_callback(data) is expected


METRO_ACTIONS = [
    *(
        f"maze_{v}"
        for v in (
            "up",
            "down",
            "left",
            "right",
            "start",
            "exit",
            "exit_accept",
            "exit_decline",
            "enter_accept",
            "enter_decline",
            "continue",
            "cancel_move",
            "first_aid",
            "first_aid_accept",
            "first_aid_decline",
            "chest_accept",
            "chest_decline",
            "npc_low_accept",
            "npc_low_decline",
            "npc_high_accept",
            "npc_high_decline",
        )
    ),
    "maze_buf_tokens_fastMove",
    "maze_buf_tokens_strong",
    "maze_buf_tokens_firstAid",
]


@pytest.mark.parametrize(
    "data",
    [
        "maze_buf_coins_fastMove",
        "maze_buf_coins_strong",
        "maze_buf_coins_firstAid",
        "maze_buf_coins_somethingNew",
        "spring_roll_coins",
        "spring_roll_coins_x10",
        "spring_regenerate",
        "mether_buy_coins",
    ],
)
def test_sw_coin_callbacks_are_donate(data: str) -> None:
    assert classify_callback(data) is D


@pytest.mark.parametrize(
    "text",
    [
        "/sb1",
        "/sb9",
        "/finish",
        "/dog_rest",
        "/coins",
        "/donations",
        "/donate",
        "/fcoins",
        "/co_backer",
        "/co_premium",
        "/co_clan_blank",
        "/co_art_grades",
        "🌐Берёзка",
        "🌐Берёзка другу",
        "+🔵 редкие",
        "+🔴 уникальные",
        "+⚪️ за 🌐",
        "💙Докупить",
    ],
)
def test_sw_coin_texts_are_donate(text: str) -> None:
    assert classify_text(text) is D


@pytest.mark.parametrize("data", ["maze_state", "maze_foo", "spring_foo", "spring_roll_smiles2"])
def test_unknown_metro_and_spring_callbacks_forbidden(data: str) -> None:
    assert classify_callback(data) is F


def test_keysbuy_forbidden() -> None:
    assert classify_text("/keysbuy") is F


@pytest.mark.parametrize("data", METRO_ACTIONS)
def test_metro_whitelist_is_action(data: str) -> None:
    assert classify_callback(data) is A


def test_spring_smiles_action_and_maze_nothing_nav() -> None:
    assert classify_callback("spring_roll_smiles") is A
    assert classify_callback("maze_nothing") is N


@pytest.mark.parametrize(
    ("text", "feature"),
    [
        ("/harvest", "deeds"),
        ("💻Работать", "deeds"),
        ("🔫Грабить", "deeds"),
        ("/read_exp", "books"),
        ("🍔Бургер", "fastfood"),
        ("/unbox_ls", "cards_containers"),
        ("/use_card", "cards_containers"),
        ("+1 🐢Мудрость", "levelup"),
        ("/tickets_all", "lottery"),
        ("💵 => 🤑", "lottery"),
        ("🍹Готовить", "smoothie"),
        ("/index_pe", "paid_info"),
        ("/ch12", "seasonal"),
        ("/t_harvest", "daily_tasks"),
        ("/t_jobMoney_hard", "daily_tasks"),
        ("⏳Задания", None),
        ("/gt", "tangerine"),
        ("👍Записаться", "factory"),
        ("/sells_piper_10", "stocks_dump"),
        ("join_fight_abcdefghijk", "bulls"),
        ("⚡️Stark Ind.", "battle"),
        ("🛡Защита", "battle"),
        ("⚔Битва", None),
        ("/inv", None),
        ("😎Я", None),
    ],
)
def test_feature_of_text(text: str, feature: str | None) -> None:
    assert feature_of_text(text) == feature


@pytest.mark.parametrize(
    ("data", "feature"),
    [
        ("gorbushka_new_accept", "gorbushka"),
        ("gorbushka_fight", "gorbushka"),
        ("gorbushka_new_decline", None),
        ("sleep_7", "sleep"),
        ("sleep_Hotel", "sleep"),
        ("maze_start", "metro"),
        ("sm_drop_3", "smoothie"),
        ("pet_feast_accept_1", "pet_feast"),
        ("buys_hooli", "stocks_dump"),
        ("t_convDets_hard_confirm", "daily_tasks"),
        ("tasksel_decline", None),
        ("rob_awake_35401851", "robbery_defense"),
    ],
)
def test_feature_of_callback(data: str, feature: str | None) -> None:
    assert feature_of_callback(data) == feature


def test_feature_names_exist_in_settings() -> None:
    from app.engine.commands import _FEATURE_CALLBACK, _FEATURE_TEXT
    from app.engine.settings import FeaturesSection

    names = {f for _, f in (*_FEATURE_TEXT, *_FEATURE_CALLBACK)}
    assert names <= set(FeaturesSection.model_fields)


def test_only_wake_click_spends_nothing() -> None:
    assert spends_nothing_callback("rob_awake_1106993")
    for data in ("rob_awake_", "gorbushka_fight", "sleep_Bridge", "t_x_hard_confirm"):
        assert not spends_nothing_callback(data)
