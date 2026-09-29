"""Своя компания после выката у автора: снимок состояния с прода 27.09 её ещё не знает, первый же
профиль с прода (☣️[SU] Fenicu) даёт ☣️Black Mesa — классы команд акций те же, что были зашиты."""

import json
from pathlib import Path

from app.engine.commands import CommandClass, classify_callback, classify_text
from app.engine.state.model import company_of, load_state
from app.engine.state.reducer import StateReducer
from tests.engine.state.helpers import feed

PROD = Path(__file__).resolve().parent.parent / "fixtures" / "api" / "state.json"


def test_prod_snapshot_learns_company_from_first_profile() -> None:
    snapshot = json.loads(PROD.read_text(encoding="utf-8"))["state"]
    assert load_state(snapshot).money is not None
    assert company_of(snapshot) is None
    # До первого профиля своей может оказаться любая: покупки акций — только с подтверждением.
    assert classify_text("/buys_stark_5", company_of(snapshot)) is CommandClass.RISKY
    after = feed(StateReducer(), snapshot, "profile", 3625071, 24 * 60)
    own = company_of(after)
    assert own == "bmesa"
    assert classify_text("/buys_bmesa_5", own) is CommandClass.RISKY
    assert classify_text("/sells_bmesa_1", own) is CommandClass.RISKY
    assert classify_callback("buys_bmesa", own) is CommandClass.RISKY
    assert classify_text("/buys_stark_5", own) is CommandClass.ACTION
    assert classify_callback("buys_umbrl", own) is CommandClass.ACTION
