import json

from tools import outlook_fixture as tool

HINT = "outlook.json устарел: uv run python tools/outlook_fixture.py"


def test_committed_outlook_fixture_is_current() -> None:
    assert json.loads(tool.OUT.read_text(encoding="utf-8")) == tool.build(), HINT
    assert json.loads(tool.OUT_STALE.read_text(encoding="utf-8")) == tool.build_stale(), HINT


def test_fixture_covers_every_plan_section() -> None:
    plan = tool.build()
    assert plan["phase"] == "busy"
    assert [c["verdict"] for c in plan["considered"]] == ["busy", "chosen"]
    assert plan["also_ready"]
    assert len(plan["wakeups"]) >= 5
    assert [(r["kind"], r["motivation"]) for r in plan["reserves"]] == [("metro", 2)]
    assert [(f["deed"], f["today"]) for f in plan["focus"]] == [
        ("deed:harvest", 3),
        ("deed:dconv", 2),
    ]


def test_stale_fixture_plans_by_data_at_busy_observation() -> None:
    plan = tool.build_stale()
    assert plan["phase"] == "unknown"
    assert plan["decision"]["scenario"] == "refresh"
    assert plan["basis_at"] == "2026-09-27T16:24:00Z"
    assert [c["verdict"] for c in plan["basis_considered"]] == ["cooldown"]
    assert [a["scenario"] for a in plan["also_ready"]] == ["tangerine", "deed:dconv"]
    assert plan["hints"]["next_deed"] == {"deed": "deed:dconv", "why": "focus"}
    loop = plan["loop"]
    assert (loop["wait_reason"], loop["wake_at"]) == ("book_ready", plan["wakeups"][0]["at"])
    assert loop["wake_at"] > plan["now"]
