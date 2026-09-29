from dataclasses import replace

import pytest

from app.engine.parsing.profile import ProfileCompact, recognize_compact
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
