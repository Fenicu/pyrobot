from app.engine.parsing.levelup import LevelUpStep, recognize_levelup
from tests.fixtures import game_msg


def test_levelup_steps() -> None:
    assert recognize_levelup(game_msg("levelup", 3532816)) == [LevelUpStep(step="menu")]
    assert recognize_levelup(game_msg("levelup", 3532818)) == [
        LevelUpStep(step="main_skill", skill="practice")
    ]
    assert recognize_levelup(game_msg("levelup", 3532820)) == [
        LevelUpStep(step="done", skill="cunning", money=142, motivation=1)
    ]
