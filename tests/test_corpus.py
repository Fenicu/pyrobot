import json
import os
from collections import Counter
from pathlib import Path

import pytest

from app.engine.events import Unrecognized
from app.engine.parsing import default_parser
from app.engine.settings import ChatsSection
from tests.fixtures import record_message

pytestmark = pytest.mark.corpus

RESEARCH = Path(os.environ.get("PYROBOT_RESEARCH", Path.home() / "pyrobot-research"))
HISTORY = RESEARCH / "raw" / "history" / "startup_bot.jsonl"
# Фаза 2 распознаёт семейства основного цикла; фаза 4 поднимет порог до 0.999.
MIN_RATIO = 0.93


@pytest.mark.skipif(not HISTORY.exists(), reason="no ~/pyrobot-research")
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
