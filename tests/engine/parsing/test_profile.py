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
