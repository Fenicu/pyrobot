import json

from tools import daily_fixture as tool

HINT = "daily.json устарел: uv run python tools/daily_fixture.py"


def test_committed_daily_fixture_is_current() -> None:
    assert json.loads(tool.OUT.read_text(encoding="utf-8")) == tool.build(), HINT


def test_fixture_covers_every_day_section() -> None:
    body = tool.build()
    days = body["days"]
    assert len(days) == tool.DAYS and days[0]["day"] == tool.TODAY.isoformat()
    today = days[0]
    assert today["partial"] and today["trophies"] == 90 and today["items"]
    assert {k["kind"] for k in today["income"]} >= {"book", "card", "task", "prizebox"}
    assert {k["kind"] for k in today["losses"]} == {"robbery", "deed_start"}
    # Повышение уровня, «нет данных» по сырью и славе, полные дни и дни до журнала прихода.
    assert any(d["level"] == {"from": 70, "to": 71} for d in days)
    assert any(d["balance"]["raw"]["delta"] is None for d in days[:7])
    assert [d["partial"] for d in days[:8]] == [
        True,
        False,
        False,
        False,
        False,
        False,
        True,
        True,
    ]
    assert body["ledger_since"] == days[6]["day"]
    assert all(d["balance"]["money"]["delta"] is None for d in days[9:])
