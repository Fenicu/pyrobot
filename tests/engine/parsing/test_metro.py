from collections import Counter
from dataclasses import replace

import pytest

from app.engine.parsing import game_recognizers
from app.engine.parsing.metro import (
    MetroBuffs,
    MetroChest,
    MetroChestOpened,
    MetroEarlyExit,
    MetroEntered,
    MetroEntrance,
    MetroExit,
    MetroFight,
    MetroFinished,
    MetroFirstAid,
    MetroLoot,
    MetroMap,
    MetroNpc,
    recognize_metro,
)
from app.engine.parsing.refusals import Refused
from app.engine.types import Button, IncomingMessage
from tests.fixtures import game_msg, game_versions

RUN = 3624441
FRAMES = game_versions("metro", RUN)
# Второй живой забег (решатель прототипа): отказы от NPC и сундука, стена, досрочный выход.
RUN2 = 3625352
FRAMES2 = game_versions("metro", RUN2)
FOUND = {
    "burger": 3,
    "hotdog": 3,
    "money": 157,
    "details": 13,
    "upgrades_white": 2,
    "knowledge": 3,
    "tokens": 29,
    "raw": 9,
    "pizza": 2,
}


def frame(n: int) -> IncomingMessage:
    return FRAMES[n]


@pytest.mark.parametrize(("frames", "total"), [(FRAMES, 533), (FRAMES2, 399)])
def test_every_frame_recognized_by_exactly_one_recognizer(
    frames: list[IncomingMessage], total: int
) -> None:
    assert len(frames) == total
    for n, msg in enumerate(frames):
        hits = [r.__name__ for r in game_recognizers() if r(msg)]
        assert hits == ["recognize_metro"], (n, hits, (msg.text or "")[:60])


def test_frame_kinds_of_recorded_run() -> None:
    kinds = Counter(type(e).__name__ for msg in FRAMES for e in recognize_metro(msg))
    assert kinds == {
        "MetroEntrance": 1,
        "MetroBuffs": 4,
        "MetroEntered": 4,
        "MetroMap": 495,
        "MetroLoot": 14,
        "MetroNpc": 3,
        "MetroFight": 3,
        "MetroFirstAid": 3,
        "MetroChest": 3,
        "MetroChestOpened": 3,
        "MetroExit": 3,
        "MetroFinished": 1,
    }
    footers = Counter(
        e.footer for msg in FRAMES for e in recognize_metro(msg) if isinstance(e, MetroMap)
    )
    assert footers == {
        "going": 246,
        "arrived": 223,
        "waiting": 20,
        "none": 3,
        "stayed": 2,
        "entry": 1,
    }


def test_entrance_and_cooldown() -> None:
    assert recognize_metro(frame(0)) == [MetroEntrance(cost=2, motivation=73)]
    assert recognize_metro(game_msg("metro", 3624531)) == [
        Refused(reason="metro_cooldown", left_s=15 * 3600 + 30 * 60)
    ]


def test_buffs_screens() -> None:
    assert recognize_metro(frame(1)) == [
        MetroBuffs(
            bought=(),
            offers=("fastMove", "strong", "firstAid"),
            tokens=10053,
            coins=0,
            token_price=20,
            can_start=True,
        ),
        MetroEntered(cost=2),
    ]
    assert recognize_metro(frame(4))[0] == MetroBuffs(
        bought=("fastMove", "strong", "firstAid"),
        offers=(),
        tokens=9993,
        coins=0,
        token_price=20,
        can_start=True,
    )


def test_buffs_with_unknown_purchase_not_recognized() -> None:
    text = (frame(2).text or "").replace("🏃Быстрый шаг за 20🕳", "🦄Невиданный баф за 20🕳")
    assert recognize_metro(replace(frame(2), text=text)) == []


def test_entry_frame() -> None:
    assert recognize_metro(frame(5)) == [
        MetroMap(
            stamina=88,
            window=("#####", "#####", "#.@.#", "###.#", "#.#.#"),
            footer="entry",
            packs=7,
        )
    ]


@pytest.mark.parametrize(
    ("n", "footer", "direction"),
    [
        (6, "going", "right"),
        (7, "arrived", "right"),
        (10, "going", "down"),
        (22, "waiting", None),
        (98, "none", None),
    ],
)
def test_map_footers(n: int, footer: str, direction: str | None) -> None:
    [screen] = recognize_metro(frame(n))
    assert isinstance(screen, MetroMap)
    assert (screen.footer, screen.direction) == (footer, direction)


def test_going_frame_keeps_old_window() -> None:
    [before] = recognize_metro(frame(5))
    [going] = recognize_metro(frame(6))
    [arrived] = recognize_metro(frame(7))
    assert isinstance(before, MetroMap) and isinstance(going, MetroMap)
    assert isinstance(arrived, MetroMap)
    assert going.window == before.window != arrived.window


def test_exit_symbol_and_stayed_footer() -> None:
    windows = [e for m in FRAMES for e in recognize_metro(m) if isinstance(e, MetroMap)]
    assert any("E" in "".join(w.window) for w in windows)
    stayed = [w for w in windows if w.footer == "stayed"]
    assert len(stayed) == 2 and all(w.packs == 4 for w in stayed)


def test_first_aid_frame_after_heal_has_new_packs() -> None:
    [healed] = recognize_metro(frame(98))
    assert isinstance(healed, MetroMap)
    assert (healed.stamina, healed.packs) == (94, 6)


def test_loot() -> None:
    loot = [e for m in FRAMES for e in recognize_metro(m) if isinstance(e, MetroLoot)]
    assert [(e.item, e.amount) for e in loot] == [
        ("burger", 2),
        ("hotdog", 2),
        ("knowledge", 6),
        ("tokens", 17),
        ("raw", 4),
        ("money", 68),
        ("burger", 1),
        ("pizza", 3),
        ("hotdog", 3),
        ("money", 79),
        ("tokens", 18),
        ("raw", 3),
        ("pizza", 2),
        ("knowledge", 3),
    ]


def test_npc_and_fight() -> None:
    assert recognize_metro(frame(47)) == [MetroNpc(strength="low")]
    assert recognize_metro(frame(48)) == [
        MetroFight(
            enemy="👨Продаваном 👨Анатолий Михайлов (71)",
            won=True,
            loot={"money": 64, "details": 9, "upgrades_white": 1},
            stamina=55,
        )
    ]


def test_strong_npc_by_buttons() -> None:
    buttons = (
        Button("⚔Сразиться", 0, 0, data="maze_npc_high_accept"),
        Button("🚶Постоять рядом", 0, 1, data="maze_npc_high_decline"),
    )
    msg = replace(frame(47), text="Ты нашёл 🦹Злодея в подземке.", inline=buttons)
    assert recognize_metro(msg) == [MetroNpc(strength="high")]


def test_chest_and_outcomes() -> None:
    assert recognize_metro(frame(167)) == [MetroChest()]
    assert recognize_metro(frame(168)) == [MetroChestOpened(result="arrow")]
    assert recognize_metro(frame(218)) == [
        MetroChestOpened(result="stash", loot={"burger": 2, "tokens": 23, "raw": 8})
    ]
    assert recognize_metro(frame(255)) == [MetroChestOpened(result="grenade")]


def test_unknown_chest_outcome_not_recognized() -> None:
    msg = replace(frame(255), text="Ты потихоньку открыл 📦Сундук.\nВнутри дракон.")
    assert recognize_metro(msg) == []


def test_first_aid_offer() -> None:
    assert recognize_metro(frame(97)) == [MetroFirstAid(packs=7, stamina=44, after=94)]
    assert recognize_metro(frame(170)) == [MetroFirstAid(packs=6, stamina=0, after=50)]


def test_exit_and_finish() -> None:
    assert recognize_metro(frame(279)) == [MetroExit(found=FOUND)]
    assert recognize_metro(frame(532)) == [
        MetroFinished(loot={**FOUND, "knowledge": 6, "pizza": 4}, stamina=100)
    ]


def test_broken_map_frames_not_recognized() -> None:
    text = frame(7).text or ""
    lines = text.split("\n")
    short = "\n".join(lines[:3] + lines[4:])
    assert recognize_metro(replace(frame(7), text=short)) == []
    odd_footer = "\n".join([*lines[:-1], "Прыгаешь"])
    assert recognize_metro(replace(frame(7), text=odd_footer)) == []
    two_players = text.replace("⬛️⬛️⬛️⬛️⬛️", "⬛️😎⬛️⬛️⬛️", 1)
    assert recognize_metro(replace(frame(7), text=two_players)) == []


def test_unknown_cell_symbol_kept_as_question_mark() -> None:
    text = (frame(5).text or "").replace("⬛️⬛️⬛️⬛️⬛️", "⬛️🐀⬛️⬛️⬛️", 1)
    [screen] = recognize_metro(replace(frame(5), text=text))
    assert isinstance(screen, MetroMap)
    assert screen.window[0] == "#?###"


def test_frame_kinds_of_second_run() -> None:
    kinds = Counter(type(e).__name__ for msg in FRAMES2 for e in recognize_metro(msg))
    assert kinds == {
        "MetroEntrance": 1,
        "MetroBuffs": 4,
        "MetroEntered": 4,
        "MetroMap": 366,
        "MetroLoot": 14,
        "MetroNpc": 3,
        "MetroFight": 2,
        "MetroChest": 3,
        "MetroChestOpened": 2,
        "MetroExit": 2,
        "MetroEarlyExit": 1,
        "MetroFinished": 1,
    }
    footers = Counter(
        e.footer for msg in FRAMES2 for e in recognize_metro(msg) if isinstance(e, MetroMap)
    )
    assert footers == {
        "going": 182,
        "arrived": 160,
        "waiting": 18,
        "stayed": 2,
        "entry": 1,
        "npc_declined": 1,
        "chest_declined": 1,
        "wall": 1,
    }


@pytest.mark.parametrize(
    ("n", "footer"), [(65, "npc_declined"), (72, "chest_declined"), (389, "wall"), (391, "stayed")]
)
def test_footers_of_staying_on_the_cell(n: int, footer: str) -> None:
    [screen] = recognize_metro(FRAMES2[n])
    assert isinstance(screen, MetroMap)
    assert (screen.footer, screen.direction) == (footer, None)


def test_stamina_above_hundred() -> None:
    [screen] = recognize_metro(FRAMES2[5])
    assert isinstance(screen, MetroMap) and screen.stamina == 205


def test_early_exit_offer() -> None:
    found = {
        "knowledge": 13,
        "pizza": 9,
        "tokens": 59,
        "hotdog": 6,
        "burger": 3,
        "money": 328,
        "details": 28,
        "upgrades_white": 2,
        "raw": 8,
    }
    half = {k: v - v // 2 for k, v in found.items()}
    assert recognize_metro(FRAMES2[390]) == [MetroEarlyExit(found=found, half=half)]
    assert recognize_metro(FRAMES2[398]) == [MetroFinished(loot=found, stamina=119)]
