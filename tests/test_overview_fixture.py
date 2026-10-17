from pathlib import Path

from pydantic import TypeAdapter

from app.api.overview_rows import OverviewAccountOut
from app.api.routes_overview import BattleHqOut

FIXTURE = Path(__file__).resolve().parent.parent / "admin/src/lib/fixtures/overview_battle.json"


def test_battle_fixture_matches_api_model() -> None:
    hq = BattleHqOut.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    assert [b.hour_msk for b in hq.battles] == [13, 22]


def test_accounts_fixture_matches_api_model() -> None:
    path = FIXTURE.with_name("overview_accounts.json")
    rows = TypeAdapter(list[OverviewAccountOut]).validate_json(path.read_text(encoding="utf-8"))
    assert [r.id for r in rows] == [1, 2, 3, 4]
