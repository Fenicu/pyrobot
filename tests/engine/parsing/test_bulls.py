from dataclasses import replace

import pytest

from app.engine.commands import CommandClass, classify_callback
from app.engine.events import Event
from app.engine.parsing import default_parser
from app.engine.parsing.bulls import (
    BullsEncounter,
    BullsInvite,
    BullsJoined,
    BullsRefused,
    BullsResult,
    recognize_bulls,
)
from app.engine.parsing.common import Rewards
from app.engine.parsing.swinfo import FactoryCall
from app.engine.settings import ChatsSection
from app.engine.state.reducer import StateReducer
from app.engine.types import Button
from tests.engine.helpers import make_msg
from tests.fixtures import game_msg

INVITE_CHAT = -1009999


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (3624430, BullsJoined(ally="☣️[SU]\xa0Defeat", enemy="🐮Быков")),
        (
            3624431,
            BullsResult(
                team=2,
                won=True,
                rewards=Rewards(exp=228, money=150, knowledge=1, stamina=88),
            ),
        ),
        (
            3525153,
            BullsResult(
                team=6,
                won=True,
                rewards=Rewards(exp=225, money=394, knowledge=1, stamina=100),
            ),
        ),
        (3610979, BullsRefused(reason="already_won")),
        (3526549, BullsRefused(reason="ended")),
        (3533483, BullsRefused(reason="missing")),
    ],
)
def test_bulls_in_game_chat(msg_id: int, expected: Event) -> None:
    assert recognize_bulls(game_msg("bulls", msg_id)) == [expected]


# Живые 03.10.2026 (прод): встречи на ночной прогулке, кнопки «⚔Драться» / «🚶Пропустить».
WALK_BULL = (
    "Гуляя по ночному городу, ты заметил 🐮Быка, проникающего в здание банка.\n"
    "За углом слышно бурное обсуждение возможностей подъёма акций.\n"
    "Выбор за тобой: звать на помощь и ⚔️драться с ними или же уйти, надеясь на то, что быки "
    "поднимут цены акций.\n"
    "\n"
    "У тебя есть 3 минуты на раздумья."
)
WALK_BEAR = (
    "Прогуливаясь, ты заметил взламывающего банкомат 🐻Медведя.\n"
    "Неподалёку ошиваются его сообщники.\n"
    "Выбор за тобой: звать друзей и ⚔драться с ними или же 🚶пропустить с миром.\n"
    "\n"
    "У тебя есть 3 минуты на раздумья."
)
FIGHT_BUTTONS = (
    Button("⚔Драться", 0, 0, "fight_accept"),
    Button("🚶Пропустить", 0, 1, "fight_decline"),
)


@pytest.mark.parametrize(
    ("text", "enemy"), [(WALK_BULL, "bull"), (WALK_BEAR, "bear")], ids=["bull", "bear"]
)
def test_walk_encounter_is_an_offer_without_reaction(text: str, enemy: str) -> None:
    msg = make_msg(text, buttons=FIGHT_BUTTONS)
    assert default_parser(ChatsSection()).parse(msg) == [
        BullsEncounter(enemy=enemy, expires_in_s=180)
    ]
    # В состояние не идёт: предложение истекает само.
    assert StateReducer().reduce({}, msg, [BullsEncounter(enemy=enemy, expires_in_s=180)]) == (
        {},
        (),
    )
    # Кнопки встречи боту запрещены.
    assert {classify_callback(b.data or "") for b in FIGHT_BUTTONS} == {CommandClass.FORBIDDEN}


def test_invite_only_in_invite_chat() -> None:
    # Единственный образец — пересылка самого игрока; в тесте это входящее сообщение товарища.
    invite = replace(game_msg("bulls_invite", 3681068), chat_id=INVITE_CHAT, outgoing=False)
    routed = default_parser(ChatsSection(bulls_invite_chat_id=INVITE_CHAT))
    assert routed.parse(invite) == [BullsInvite(code="join_fight_AaBH89kYd2J")]
    assert default_parser(ChatsSection()).parse(invite) == []


def test_invite_chat_shared_with_swinfo() -> None:
    general = -1001109615116
    parser = default_parser(ChatsSection(bulls_invite_chat_id=general))
    invite = replace(game_msg("bulls_invite", 3681068), from_id=5, outgoing=False)
    assert parser.parse(invite) == [BullsInvite(code="join_fight_AaBH89kYd2J")]
    assert parser.parse(game_msg("swinfo", 3817108)) == [FactoryCall()]


def test_invite_without_button_ignored() -> None:
    invite = replace(game_msg("bulls_invite", 3681068), chat_id=INVITE_CHAT, inline=())
    routed = default_parser(ChatsSection(bulls_invite_chat_id=INVITE_CHAT))
    assert routed.parse(invite) == []


def test_invite_chat_shared_with_swinfo_random_message_ignored() -> None:
    # Общий чат: сообщение от случайного участника, не от SWINFO, без кнопки инвайта.
    general = -1001109615116
    parser = default_parser(ChatsSection(bulls_invite_chat_id=general))
    random_msg = replace(
        game_msg("bulls_invite", 3681068),
        chat_id=general,
        from_id=5,
        inline=(),
        text="привет, кто-нибудь ещё играет?",
    )
    assert parser.parse(random_msg) == []
