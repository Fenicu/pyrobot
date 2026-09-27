import json
import os
from collections import Counter
from pathlib import Path

import pytest

from app.engine.events import Unrecognized
from app.engine.parsing import default_parser
from app.engine.parsing.common import parse_rewards
from app.engine.parsing.daily import DailyTasksScreen, TaskCompleted, recognize_daily
from app.engine.parsing.lottery import (
    LotteryBought,
    LotteryCurrency,
    LotteryOff,
    LotteryScreen,
    recognize_lottery,
)
from app.engine.parsing.screens import LotteryWin
from app.engine.parsing.smoothie import SmoothieRecipe
from app.engine.parsing.swinfo import BattleSummary
from app.engine.settings import ChatsSection
from tests.fixtures import record_message

pytestmark = pytest.mark.corpus

RESEARCH = Path(os.environ.get("PYROBOT_RESEARCH", Path.home() / "pyrobot-research"))
HISTORY = RESEARCH / "raw" / "history" / "startup_bot.jsonl"
SWINFO = RESEARCH / "raw" / "history" / "startup_main_swinfo.jsonl"
CHANNEL = RESEARCH / "raw" / "history" / "smoothie_channel.jsonl"
SEARCH = RESEARCH / "raw" / "search" / "game.jsonl"
no_research = pytest.mark.skipif(not HISTORY.exists(), reason="no ~/pyrobot-research")
MIN_RATIO = 0.999


@no_research
def test_corpus_recognition_ratio() -> None:
    parser = default_parser(ChatsSection())
    total = recognized = 0
    misses: Counter[str] = Counter()
    with HISTORY.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("out") or not rec.get("text"):
                continue
            total += 1
            events = parser.parse(record_message(rec))
            if any(isinstance(e, Unrecognized) for e in events):
                misses[rec["text"].split("\n", 1)[0][:60]] += 1
            else:
                recognized += 1
    ratio = recognized / total
    top = "\n".join(f"{n:5} {line}" for line, n in misses.most_common(15))
    print(f"\ncorpus: {recognized}/{total} = {ratio:.4f}\n{top}")
    assert ratio >= MIN_RATIO


def _records(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


@no_research
def test_swinfo_battle_summaries_have_all_prices() -> None:
    parser = default_parser(ChatsSection())
    summaries = [
        e
        for rec in _records(SWINFO)
        for e in parser.parse(record_message(rec))
        if isinstance(e, BattleSummary)
    ]
    assert len(summaries) > 900
    assert all(len(e.prices) == 6 for e in summaries)


@no_research
def test_every_channel_recipe_parsed() -> None:
    parser = default_parser(ChatsSection())
    posts = [r for r in _records(CHANNEL) if str(r.get("text") or "").startswith("Рецепт: ")]
    assert posts
    for rec in posts:
        assert any(isinstance(e, SmoothieRecipe) for e in parser.parse(record_message(rec)))


@no_research
def test_every_lottery_win_has_a_prize() -> None:
    parser = default_parser(ChatsSection())
    wins = [
        e
        for rec in _records(HISTORY)
        if not rec.get("out")
        for e in parser.parse(record_message(rec))
        if isinstance(e, LotteryWin)
    ]
    assert wins
    for win in wins:
        prize = (win.rewards.knowledge, win.rewards.raw, win.rewards.details, win.motivation)
        assert any(prize) or win.containers_small or win.containers_medium or win.skills


@pytest.mark.skipif(not SEARCH.exists(), reason="no search export in ~/pyrobot-research")
def test_every_daily_tasks_message_in_search_parsed() -> None:
    """Экраны заданий за 2019–2026, сообщения о выполнении и строки личного прогресса."""
    seen: Counter[str] = Counter()
    kinds = (("⏳Ежедневные задания", DailyTasksScreen), ("Ты завершил задание", TaskCompleted))
    with SEARCH.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            text = str(rec.get("text") or "")
            if rec.get("out"):
                continue
            for prefix, event in kinds:
                if text.startswith(prefix):
                    seen[event.kind] += 1
                    events = recognize_daily(record_message(rec))
                    assert [type(e) for e in events] == [event], rec["id"]
            if "🔜Личное задание" in text:
                seen["line"] += 1
                assert parse_rewards(text).personal_task is not None, rec["id"]
    assert seen["daily_tasks_screen"] > 5000 and seen["task_completed"] > 1000
    assert seen["line"] > 5000


@pytest.mark.skipif(not SEARCH.exists(), reason="no search export in ~/pyrobot-research")
def test_every_lottery_message_parsed() -> None:
    """Экраны тиража, ответы «Купить все» и экраны покупки за валюту за 2019–2026 — целиком."""
    kinds = (
        ("Лотерея - ", (LotteryScreen, LotteryBought)),
        ("Покупка билетов за ", (LotteryCurrency,)),
        ("❌Лотерея пока не проводится", (LotteryOff,)),
    )
    seen: Counter[str] = Counter()
    for path in (SEARCH, HISTORY):
        for rec in _records(path):
            text = str(rec.get("text") or "")
            if rec.get("out") or rec.get("chat") != 227859379:
                continue
            for prefix, allowed in kinds:
                if text.startswith(prefix):
                    events = recognize_lottery(record_message(rec))
                    assert len(events) == 1 and isinstance(events[0], allowed), rec["id"]
                    seen[events[0].kind] += 1
    assert seen["lottery_bought"] > 2000 and seen["lottery_screen"] > 20
    assert seen["lottery_currency"] >= 2 and seen["lottery_off"] >= 5
