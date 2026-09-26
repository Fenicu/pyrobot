from app.engine.parsing.battle import BattleTargetSet, recognize_battle_target
from tests.fixtures import game_msg


def test_target_set() -> None:
    assert recognize_battle_target(game_msg("battle", 3624402)) == [
        BattleTargetSet(target="📯Pied Piper", battle_in_s=9840, zero_stamina=False)
    ]


def test_target_set_with_zero_stamina() -> None:
    assert recognize_battle_target(game_msg("battle", 3516891)) == [
        BattleTargetSet(target="📯Pied Piper", battle_in_s=745200, zero_stamina=True)
    ]


def test_defense() -> None:
    assert recognize_battle_target(game_msg("battle", 3569475)) == [
        BattleTargetSet(target="🛡Защита", battle_in_s=34560, zero_stamina=False)
    ]
