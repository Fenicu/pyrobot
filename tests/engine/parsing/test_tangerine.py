import pytest

from app.engine.parsing import default_parser
from app.engine.parsing.tangerine import TangerineReceived, TangerineRefused, recognize_tangerine
from app.engine.settings import ChatsSection
from tests.engine.helpers import make_msg
from tests.fixtures import game_msg

# Живые 03–05.10.2026 (прод): подаренные мандаринки — фраза дарителя всякий раз своя.
GIFTS = (
    (
        "👍Ура! ☣️[SU]\xa0Fenicu со словами «Вот я тебе даю сейчас, а ты мне потом» подарил тебе "
        "🍊мандаринки +2 шт.",
        "Fenicu",
        2,
    ),
    (
        '👍Ура! ☣️[SU]\xa0Lolichanmay с криком "Вот тебе мандарин, только не убивай!" подарил тебе '
        "🍊мандаринки +2 шт.",
        "Lolichanmay",
        2,
    ),
    ("👍Ура! ☣️[SU]\xa0Fenicu судорожно трясясь подарил тебе 🍊мандаринки +1 шт.", "Fenicu", 1),
    (
        "👍Ура! ☣️[SU]\xa0Lolichanmay выхватил у Кима и подарил тебе 🍊мандаринки +1 шт.",
        "Lolichanmay",
        1,
    ),
    (
        "👍Ура! ☣️[SU]\xa0Fenicu отобрал у бродяги под мостом и подарил тебе 🍊мандаринки +1 шт.",
        "Fenicu",
        1,
    ),
    ("👍Ура! ☣️[SU]\xa0Lolichanmay по-братски подарил тебе 🍊мандаринки +1 шт.", "Lolichanmay", 1),
)


def test_not_player_and_cooldown() -> None:
    assert recognize_tangerine(game_msg("tangerine", 3599304)) == [
        TangerineRefused(reason="not_player", target="𝐿𝑜𝓁𝒾𝒸𝒽𝒶𝓃𝓂𝒶𝓎")
    ]
    assert recognize_tangerine(game_msg("tangerine", 3616906)) == [
        TangerineRefused(reason="cooldown", left_s=19 * 3600 + 54 * 60)
    ]


@pytest.mark.parametrize(("text", "sender", "count"), GIFTS)
def test_received_gift(text: str, sender: str, count: int) -> None:
    assert default_parser(ChatsSection()).parse(make_msg(text)) == [
        TangerineReceived(sender=sender, count=count)
    ]


@pytest.mark.parametrize(
    ("text", "sender"),
    [
        ("👍Ура! ☂️MstrGreen подарил тебе 🍊мандаринки +3 шт.", "MstrGreen"),
        ("👍Ура! [SU]\xa0𝐿𝑜𝓁𝒾𝒸𝒽𝒶𝓃𝓂𝒶𝓎 подарил тебе 🍊мандаринки +3 шт.", "𝐿𝑜𝓁𝒾𝒸𝒽𝒶𝓃𝓂𝒶𝓎"),
    ],
)
def test_received_gift_name_without_tag_or_mark(text: str, sender: str) -> None:
    assert recognize_tangerine(make_msg(text)) == [TangerineReceived(sender=sender, count=3)]
