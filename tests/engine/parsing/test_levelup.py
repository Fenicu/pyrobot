from datetime import UTC, datetime

from app.engine.parsing.levelup import (
    LevelUpStep,
    MotivationCapRaised,
    recognize_levelup,
    recognize_referral,
)
from app.engine.types import IncomingMessage
from tests.fixtures import game_msg

# Прод 07.10, аккаунт 3.
REFERRAL = "Fenikode достиг 15 уровня.\nТы получаешь +1 к запасу 🔥Мотивации"
REFERRAL2 = "Ivan достиг 3 уровня.\nТы получаешь +2 к запасу 🔥Мотивации"


def _msg(text: str) -> IncomingMessage:
    moment = datetime(2026, 10, 7, 18, 6, tzinfo=UTC)
    return IncomingMessage(
        chat_id=227859379,
        msg_id=0,
        revision=0,
        kind="new",
        date=moment,
        received_at=moment,
        text=text,
    )


def test_levelup_steps() -> None:
    assert recognize_levelup(game_msg("levelup", 3532816)) == [LevelUpStep(step="menu")]
    assert recognize_levelup(game_msg("levelup", 3532818)) == [
        LevelUpStep(step="main_skill", skill="practice")
    ]
    assert recognize_levelup(game_msg("levelup", 3532820)) == [
        LevelUpStep(step="done", skill="cunning", money=142, motivation=1)
    ]


def test_referral_raises_motivation_cap() -> None:
    assert recognize_referral(_msg(REFERRAL)) == [
        MotivationCapRaised(name="Fenikode", level=15, amount=1)
    ]
    assert recognize_referral(_msg(REFERRAL2)) == [
        MotivationCapRaised(name="Ivan", level=3, amount=2)
    ]
