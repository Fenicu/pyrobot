from collections.abc import Iterator

import pytest

from app.engine.planner.decide import _Planner
from app.engine.planner.types import Act, Decision


@pytest.fixture(autouse=True)
def outlook_matches_decide(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Decision]]:
    """Каждое решение тестов планировщика сверяется с `outlook` на тех же входах: «План бота»
    показывает ровно то решение, которое принял бы цикл. Список — сверенные решения."""
    checked: list[Decision] = []
    original = _Planner.decide

    def twin(p: _Planner) -> _Planner:
        return _Planner(
            p.s,
            p.cfg,
            p.now,
            p.certified,
            p.last_refresh,
            p.cooldowns,
            p.last_done,
            p.metro_durations,
            p.done_today,
        )

    def decide(self: _Planner) -> Decision:
        decision = original(self)
        view = twin(self).outlook(lambda: twin(self))
        assert view.decision == decision
        assert view.considered == decision.candidates
        # Второй проход по последним данным своего «выбрано» не несёт: выбор — только у решения.
        basis = view.basis.considered if view.basis is not None else ()
        chosen = [c for c in view.considered + basis if c.verdict == "chosen"]
        assert len(chosen) == (1 if isinstance(decision, Act) else 0)
        checked.append(decision)
        return decision

    monkeypatch.setattr(_Planner, "decide", decide)
    yield checked
