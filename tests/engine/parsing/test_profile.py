from dataclasses import replace

import pytest

from app.engine.parsing import game_recognizers
from app.engine.parsing.battle import DEFENSE
from app.engine.parsing.profile import ProfileCompact, recognize_compact
from tests.engine import trip_texts
from tests.fixtures import game_msg


def _profile(msg_id: int) -> ProfileCompact:
    events = recognize_compact(game_msg("profile", msg_id))
    assert len(events) == 1 and isinstance(events[0], ProfileCompact)
    return events[0]


def test_full_compact_profile() -> None:
    p = _profile(3610633)
    assert (p.battle_in_s, p.level, p.exp, p.exp_next) == (23880, 71, 17173536, 18155142)
    assert (p.money, p.stamina, p.knowledge, p.raw, p.details) == (1470, 100, 21173, 20293, 131778)
    assert (p.motivation, p.motivation_max, p.motivation_next_in_s) == (63, 85, 3180)
    assert (p.bag, p.bag_cap, p.tangerines) == (10, 24, 2)
    assert (p.practice, p.theory, p.cunning, p.wisdom) == (461, 460, 344, 345)
    assert (p.battle_target, p.sleep_in_s, p.busy_kind, p.busy_left_s) == (
        "📯Pied Piper",
        3480,
        None,
        None,
    )


def test_swcoin_and_holidays() -> None:
    p = _profile(3516674)
    assert (p.battle_in_s, p.money, p.motivation_max, p.sleep_in_s) == (835200, 823, 84, 183600)


def test_sleeping_under_bridge_without_blank_line() -> None:
    p = _profile(3623107)
    assert (p.busy_kind, p.busy_left_s, p.sleep_in_s) == ("sleep_bridge", 25080, None)
    assert p.battle_target == "📯Pied Piper"


def test_ceo_prefix_stripped() -> None:
    p = _profile(3536910)
    assert (p.money, p.busy_kind, p.busy_left_s, p.battle_target) == (
        517,
        "sleep_bridge",
        41640,
        None,
    )


def test_doing_variants() -> None:
    assert (_profile(3623657).busy_kind, _profile(3623657).busy_left_s) == ("harvest", 126)
    assert (_profile(3624868).busy_kind, _profile(3624868).busy_left_s) == ("dconv", 13)
    assert (_profile(3625071).busy_kind, _profile(3625071).busy_left_s) == ("darts", 2)
    assert (_profile(3623869).busy_kind, _profile(3623869).busy_left_s) == ("job", 41)
    assert _profile(3624478).busy_kind is None


def test_foreign_text_ignored() -> None:
    assert recognize_compact(game_msg("battle", 3624402)) == []


def test_other_busy_variants() -> None:
    assert (_profile(3603794).busy_kind, _profile(3603794).busy_left_s) == ("learn", 108)
    assert (_profile(3620232).busy_kind, _profile(3620232).busy_left_s) == ("eat", 169)
    assert (_profile(3618555).busy_kind, _profile(3618555).busy_left_s) == ("fight", 245)
    assert (_profile(3586615).busy_kind, _profile(3586615).busy_left_s) == ("sleep_hotel", 3120)


def _with_mark(mark: str) -> ProfileCompact | None:
    msg = game_msg("profile", 3624478)
    text = (msg.text or "").replace("☣️[SU]", f"{mark}[SU]", 1)
    events = recognize_compact(replace(msg, text=text))
    return events[0] if events and isinstance(events[0], ProfileCompact) else None


def test_own_company_from_mark_before_name() -> None:
    # Своя компания — значок перед тегом команды: у автора ☣️ — Black Mesa.
    assert _profile(3624478).company == "bmesa"
    assert _profile(3536910).company == "bmesa"
    marks = {"📯": "piper", "🤖": "hooli", "⚡️": "stark", "☂️": "umbrl", "🎩": "wayne"}
    # Без VS16 значок тот же; рядом со значком компании бывают и другие (💰).
    marks |= {"⚡": "stark", "☂️💰": "umbrl"}
    assert {mark: _company(mark) for mark in marks} == marks


def _company(mark: str) -> str | None:
    profile = _with_mark(mark)
    assert profile is not None, mark
    return profile.company


def test_unknown_company_mark_keeps_profile() -> None:
    assert _company("") is None and _company("🦄") is None
    profile = _with_mark("")
    assert profile is not None and profile.money == _profile(3624478).money


def test_team_tag_from_name_line() -> None:
    assert _profile(3624478).team_tag == "SU"
    assert _profile(3536910).team_tag == "SU"


def _teamless(name_line: str) -> ProfileCompact:
    # Своего профиля вне команды в корпусе нет (автор в команде с 2023, прежний формат профиля
    # другой): строка имени — реальная, тег убран. Так игра пишет игроков без команды в отчётах
    # битв SWINFO («☂️MstrGreen», «📯🕺Макс») и биржевиков («📯Stiven King (54)»).
    msg = game_msg("profile", 3624478)
    text = (msg.text or "").replace("☣️[SU]\xa0Fenicu 🐀", name_line, 1)
    assert text != msg.text
    events = recognize_compact(replace(msg, text=text))
    assert len(events) == 1 and isinstance(events[0], ProfileCompact), name_line
    return events[0]


@pytest.mark.parametrize(
    ("line", "company"),
    [
        ("☣️Fenicu 🐀", "bmesa"),
        ("☣️💰Fenicu 🐀", "bmesa"),
        ("📯🕺Макс", "piper"),
        ("📯Stiven King 🐕", "piper"),
        ("⚡️1", "stark"),
        # В имени без тега бывают и значки компаний: своя — первый значок строки.
        ("☂️🤖Robot 🐀", "umbrl"),
    ],
)
def test_profile_without_team_tag(line: str, company: str) -> None:
    p = _teamless(line)
    own = _profile(3624478)
    assert p.team_tag is None and p.company == company
    assert replace(p, team_tag=own.team_tag, company=own.company) == own


# Профиль нового персонажа без профессии (аккаунт 2): опыт в скобках строки уровня, без 🧵.
_NO_PROFESSION = (
    "🎙CEO:\n🛠 - 🍋🥕🍅🍏🍅 (23/08) - /del\n\nБитва через 8ч. 18 мин.!\n\n⚡️Frosty\n"
    "🎚11 (881 из 1\xa0110💡)\n💵$274 🔋0% /to_eat\n📚4\xa0\xa0 🔩64\xa0\xa0 ⚙️29\n"
    "🔥7 из 7 (/pr)\n🎒1 из 12 /inv\n\n🔨\xa013    🎓\xa011\n🐿\xa06    🐢\xa06\n"
    "⭐️⭐️⭐️ /cool\n\n🛌 Через какое-то время\nПолный профиль /full"
)


def _recognized(text: str, base: int = 3624478) -> ProfileCompact:
    events = recognize_compact(replace(game_msg("profile", base), text=text))
    assert len(events) == 1 and isinstance(events[0], ProfileCompact)
    return events[0]


def test_profile_without_profession() -> None:
    assert _recognized(_NO_PROFESSION) == ProfileCompact(
        battle_in_s=29880,
        level=11,
        exp=881,
        exp_next=1110,
        money=274,
        stamina=0,
        knowledge=4,
        raw=64,
        details=29,
        motivation=7,
        motivation_max=7,
        motivation_next_in_s=None,
        bag=1,
        bag_cap=12,
        tangerines=None,
        practice=13,
        theory=11,
        cunning=6,
        wisdom=6,
        battle_target=None,
        sleep_in_s=None,
        busy_kind=None,
        busy_left_s=None,
        company="stark",
        team_tag=None,
    )


def test_profile_without_profession_and_ceo_block() -> None:
    plain = _NO_PROFESSION[_NO_PROFESSION.index("Битва через") :]
    assert _recognized(plain) == _recognized(_NO_PROFESSION)


# Профиль с профессией без подпрофессии в скобках (аккаунт 3): «🧵7» вместо «🧵16 (🪡)».
_BARE_PROFESSION = (
    "Битва через 4ч. 8 мин.!\n\n☣️[SU]\xa0Lolichanmay 🐀\n🎚54   🧵7\n"
    "💡1\xa0647\xa0419 из 1\xa0664\xa0167\n💵$3\xa0475 🔋100% /to_eat\n"
    "📚16\xa0602\xa0\xa0 🔩18\xa0265\xa0\xa0 ⚙️25\xa0438\n🔥66 из 66 (/pr)\n🎒9 из 20 /inv\n"
    "🍊90 /gifts\n\n🔨\xa0287    🎓\xa0284\n🐿\xa0186    🐢\xa0222\n⭐️⭐️⭐️ /cool\n"
    "🛡Защита\n🛌Спишь под мостом (6ч. 28 мин.)\nПолный профиль /full"
)


def test_profile_with_bare_profession() -> None:
    assert _recognized(_BARE_PROFESSION) == ProfileCompact(
        battle_in_s=14880,
        level=54,
        exp=1647419,
        exp_next=1664167,
        money=3475,
        stamina=100,
        knowledge=16602,
        raw=18265,
        details=25438,
        motivation=66,
        motivation_max=66,
        motivation_next_in_s=None,
        bag=9,
        bag_cap=20,
        tangerines=90,
        practice=287,
        theory=284,
        cunning=186,
        wisdom=222,
        battle_target=DEFENSE,
        sleep_in_s=None,
        busy_kind="sleep_bridge",
        busy_left_s=23280,
        company="bmesa",
        team_tag="SU",
    )


def test_profile_with_bare_profession_and_ceo_block() -> None:
    ceo = (
        "🎙CEO:\nТы молодец. Ходи в битвы, не забывай про репорты. Приятной игры! "
        "/harvest - /del\n\n"
    )
    p = _recognized(ceo + _BARE_PROFESSION)
    assert p == _recognized(_BARE_PROFESSION)
    assert p.battle_target == DEFENSE


def test_level_line_formats_give_same_profile() -> None:
    own = game_msg("profile", 3624478).text or ""
    old = "🎚71   🧵16 (🪡)\n💡17\xa0496\xa0049 из 18\xa0155\xa0142\n"
    assert old in own
    short = own.replace(old, "🎚71 (17\xa0496\xa0049 из 18\xa0155\xa0142💡)\n")
    assert _recognized(short) == _profile(3624478)


@pytest.mark.parametrize(
    ("sleep_line", "seconds"),
    [("🛌 Через 2ч. 5 мин.", 7500), ("🛌 Через какое-то время", None)],
)
def test_sleep_in_needs_real_duration(sleep_line: str, seconds: int | None) -> None:
    own = game_msg("profile", 3624478).text or ""
    assert "🛌 Через 2д. 11ч." in own
    p = _recognized(own.replace("🛌 Через 2д. 11ч.", sleep_line))
    assert p.sleep_in_s == seconds
    assert replace(p, sleep_in_s=_profile(3624478).sleep_in_s) == _profile(3624478)


@pytest.mark.parametrize(
    ("line", "left_s"),
    [(trip_texts.PROFILE_TRAM_LINE, 9 * 60 + 58), (trip_texts.PROFILE_SLED_LINE, 9 * 60 + 11)],
)
def test_trip_in_profile_is_busy_trip(line: str, left_s: int) -> None:
    msg = game_msg("profile", 3623869)
    assert msg.text is not None
    text = msg.text.replace("💻Работаешь (41 сек.)", line)
    [p] = recognize_compact(replace(msg, text=text))
    assert isinstance(p, ProfileCompact)
    assert (p.busy_kind, p.busy_left_s) == ("trip", left_s)


# Профессия «Скупщик»: в строке уровня свой значок (💼), а не 🧵 — профиль новых аккаунтов с прода.
_BUYER = (
    "🎙CEO:\n"
    "Ты молодец. Ходи в битвы, не забывай про репорты. Приятной игры! /harvest - /del\n"
    "\n"
    "Битва через 8ч. 20 мин.!\n"
    "\n"
    "☣️[SU]\xa0Casadei 🐕\n"
    "🎚71   💼16 (💠)\n"
    "💡17\xa0432\xa0582 из 18\xa0155\xa0142\n"
    "💵$228 🌐3 🔋100% /to_eat\n"
    "📚27\xa0241\xa0\xa0 🔩34\xa0247\xa0\xa0 ⚙️37\xa0881\n"
    "🔥26 из 26 (/pr)\n"
    "🎒12 из 24 /inv\n"
    "🍊67 /gifts\n"
    "\n"
    "🔨\xa0442    🎓\xa0442\n"
    "🐿\xa0315    🐢\xa0316\n"
    "⭐️⭐️⭐️ /cool\n"
    "🛌Спишь под мостом (11ч. 40 мин.)\n"
    "Полный профиль /full\n"
)


def test_profile_with_other_profession_icon() -> None:
    p = _recognized(_BUYER)
    assert (p.level, p.exp, p.exp_next, p.money) == (71, 17432582, 18155142, 228)
    assert (p.busy_kind, p.company, p.team_tag) == ("sleep_bridge", "bmesa", "SU")


# Ответ игры на /main (06.10, аккаунт 1): компактный профиль и главная reply-клавиатура.
MAIN_ANSWER = (
    "Битва через 4ч. 21 мин.!\n\n"
    "☣️[SU]\xa0Fenicu 🐀\n"
    "🎚71   🧵16 (🪡)\n"
    "💡17\xa0663\xa0927 из 18\xa0155\xa0142\n"
    "💵$555 🔋100% /to_eat\n"
    "📚22\xa0391\xa0\xa0 🔩22\xa0390\xa0\xa0 ⚙️137\xa0050\n"
    "🔥0 из 85 (/pr) (26 мин.)\n"
    "🎒11 из 24 /inv\n"
    "🍊8 /gifts\n\n"
    "🔨\xa0461    🎓\xa0460\n"
    "🐿\xa0346    🐢\xa0346\n"
    "⭐️⭐️⭐️ /cool\n\n"
    "🛌 Через 2д. 14ч.\n"
    "🛡Защита\n"
    "Полный профиль /full"
)
MAIN_KEYBOARD = (
    ("😎Я", "⚔Битва", "🏢Офис"),
    ("🎒Рюкзак", "🧬Вирусы", "🐾Петы"),
    ("🕸Сеть", "⏳Дела", "👫Команда"),
)


def test_main_answer_is_compact_profile() -> None:
    msg = replace(
        game_msg("profile", 3624478), text=MAIN_ANSWER, inline=(), reply_kb=MAIN_KEYBOARD
    )
    assert [r.__name__ for r in game_recognizers() if r(msg)] == ["recognize_compact"]
    [p] = recognize_compact(msg)
    assert isinstance(p, ProfileCompact)
    assert (p.battle_in_s, p.level, p.money, p.stamina, p.motivation) == (15660, 71, 555, 100, 0)
    assert (p.company, p.team_tag, p.battle_target) == ("bmesa", "SU", DEFENSE)
