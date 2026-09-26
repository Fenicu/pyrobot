from app.engine.parsing.tangerine import TangerineRefused, recognize_tangerine
from tests.fixtures import game_msg


def test_not_player_and_cooldown() -> None:
    assert recognize_tangerine(game_msg("tangerine", 3599304)) == [
        TangerineRefused(reason="not_player", target="𝐿𝑜𝓁𝒾𝒸𝒽𝒶𝓃𝓂𝒶𝓎")
    ]
    assert recognize_tangerine(game_msg("tangerine", 3616906)) == [
        TangerineRefused(reason="cooldown", left_s=19 * 3600 + 54 * 60)
    ]
