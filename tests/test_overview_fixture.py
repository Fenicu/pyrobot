from pathlib import Path

from app.api.routes_overview import BattleHqOut

FIXTURE = Path(__file__).resolve().parent.parent / "admin/src/lib/fixtures/overview_battle.json"


def test_battle_fixture_matches_api_model() -> None:
    hq = BattleHqOut.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    assert [b.hour_msk for b in hq.battles] == [13, 22]
