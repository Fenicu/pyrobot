import importlib
import pkgutil

import tests.engine.scenarios as package
from app.engine.scenarios.registry import CERTIFIED, SCENARIOS


def _certified_by_tests() -> set[str]:
    names: set[str] = set()
    for info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        for value in vars(module).values():
            names.update(getattr(value, "__certifies__", ()))
    return names


def test_every_certified_scenario_has_a_test() -> None:
    assert CERTIFIED <= _certified_by_tests()


def test_certified_names_exist() -> None:
    assert CERTIFIED <= set(SCENARIOS)


def test_uncertified_scenarios_are_not_claimed() -> None:
    assert _certified_by_tests() <= CERTIFIED


def test_daily_pick_needs_task_name() -> None:
    spec = SCENARIOS["daily_pick"]
    assert spec.invalid({"task": "convDets_hard"}) == []
    assert spec.invalid({}) == ["task"]
    assert spec.invalid({"task": "convDets"}) == ["task"]
    assert spec.invalid({"task": "/t_convDets_hard"}) == ["task"]
