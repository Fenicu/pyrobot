import json

from tools import outlook_fixture as tool

HINT = "outlook.json устарел: uv run python tools/outlook_fixture.py"


def test_committed_outlook_fixture_is_current() -> None:
    assert json.loads(tool.OUT.read_text(encoding="utf-8")) == tool.build(), HINT


def test_fixture_covers_every_plan_section() -> None:
    plan = tool.build()
    assert plan["phase"] == "busy"
    assert [c["verdict"] for c in plan["considered"]] == ["busy", "chosen"]
    assert plan["also_ready"]
    assert len(plan["wakeups"]) >= 5
    assert [(f["deed"], f["today"]) for f in plan["focus"]] == [
        ("deed:harvest", 3),
        ("deed:dconv", 2),
    ]
