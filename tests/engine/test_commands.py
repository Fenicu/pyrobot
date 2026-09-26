import pytest

from app.engine.commands import CommandClass, classify_callback, classify_text

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
        ("sleep_13", F),
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
        ("mether_buy_coins", D),
        ("unknown_cb", F),
    ],
)
def test_classify_callback(data: str, expected: CommandClass) -> None:
    assert classify_callback(data) is expected
