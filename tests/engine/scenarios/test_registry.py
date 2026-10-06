import importlib
import pkgutil
import re
from typing import Any

import pytest

import tests.engine.scenarios as package
from app.engine.gateway.gateway import GADGET_SCENARIOS
from app.engine.scenarios.registry import CERTIFIED, SCENARIOS, Param, ScenarioSpec


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


@pytest.mark.parametrize("name", ["daily_pick", "team_pick"])
def test_task_pick_needs_task_name(name: str) -> None:
    spec = SCENARIOS[name]
    assert spec.invalid({"task": "convDets_hard"}) == []
    assert spec.invalid({}) == ["task"]
    assert spec.invalid({"task": "convDets"}) == ["task"]
    assert spec.invalid({"task": "/t_convDets_hard"}) == ["task"]
    assert spec.invalid({"task": "/ts_convDets_hard"}) == ["task"]
    assert name in CERTIFIED


def required(scenario: str, name: str) -> Param:
    return SCENARIOS[scenario].required[name]


@pytest.mark.parametrize(
    ("scenario", "name", "good", "bad"),
    [
        ("sleep", "hours", [7, 12], [6, 13, True, 7.0, "7", None]),
        ("stocks_dump", "keep", [0, 10_000], [-1, False, 1.5]),
        ("tangerine", "chat", [-1001377961602, 0], [True, "1", 1.0]),
        ("refresh", "source", ["profile", "gorbushka", "artifacts"], ["bank", "", 1, None]),
        (
            "battle_target",
            "target",
            ["📯Pied Piper", "☣️Black Mesa", "🛡Защита"],
            ["Hooli", "piper"],
        ),
        (
            "daily_pick",
            "task",
            ["convDets_hard", "jobMoney_easy"],
            ["convDets", "/t_convDets_hard", "convDets_hard\n", "conv_Dets_hard", "x_hard "],
        ),
        (
            "bulls_join",
            "code",
            ["join_fight_AaBH89kYd2J", "join_fight_a-b_c-d_e-f"],
            ["join_fight_short", "join_fight_AaBH89kYd2J\n", "join_fight_AaBH89kYd2Jx", 1],
        ),
        (
            "smoothie",
            "recipe",
            ["🍇🥕🥕🍋🍅", "🍋🍋🍋🍋🍋"],
            ["🍇🥕🥕🍋", "🍇🥕🥕🍋🍅🍅", "🍇🥕🥕🍋🍌", 5, ""],
        ),
        ("artifact_start", "artifact", ["book", "fax", "light"], ["box", "", None, 1]),
        (
            "trip",
            "vehicle",
            ["car", "tram", "sled", "bike", "scooter", "tractor"],
            ["🚲Велик", "bicycle", "Car", "", None, 1],
        ),
    ],
)
def test_required_checks_keep_their_edges(
    scenario: str, name: str, good: list[Any], bad: list[Any]
) -> None:
    param = required(scenario, name)
    assert all(param(v) for v in good)
    assert not any(param(v) for v in bad)


def test_catalog_specs() -> None:
    assert required("sleep", "hours").spec() == {"type": "int", "min": 7, "max": 12}
    assert required("stocks_dump", "margin").spec() == {"type": "int", "min": 0}
    assert required("tangerine", "reply_to").spec() == {"type": "int"}
    assert required("refresh", "source").spec() == {
        "type": "enum",
        "values": [
            "artifacts",
            "food",
            "gifts",
            "gorbushka",
            "inventory",
            "profile",
            "stocks",
            "upgrades",
        ],
    }
    assert required("daily_pick", "task").spec() == {
        "type": "string",
        "pattern": "^[A-Za-z]+_(?:easy|medium|hard)$",
    }


def test_patterns_are_portable_and_agree_with_checks() -> None:
    samples = {
        ("daily_pick", "task"): ["convDets_hard", "convDets", "a_medium", "a_b_hard", "_hard"],
        ("bulls_join", "code"): ["join_fight_AaBH89kYd2J", "join_fight_short", "join_fightAaBH"],
        ("smoothie", "recipe"): ["🍇🥕🥕🍋🍅", "🍇🥕🥕🍋", "🍌🍌🍌🍌🍌", "🍏🍏🍏🍏🍏🍏"],
    }
    for (scenario, name), values in samples.items():
        param = required(scenario, name)
        assert param.pattern is not None
        # Синтаксис ECMA-262: без \A, \Z и именованных групп Python.
        assert not re.search(r"\\[AZ]|\(\?P", param.pattern), param.pattern
        compiled = re.compile(param.pattern)
        for value in values:
            assert bool(compiled.match(value)) == param(value), (scenario, value)


def test_trip_scenarios() -> None:
    assert required("trip", "vehicle").spec() == {
        "type": "enum",
        "values": ["bike", "car", "scooter", "sled", "tractor", "tram"],
    }
    assert SCENARIOS["trip"].invalid({}) == ["vehicle"]
    assert SCENARIOS["trips_refresh"].required == {}
    assert {"trip", "trips_refresh"} <= CERTIFIED


def test_manual_flag_defaults_true() -> None:
    async def fn(ctx: Any, state: Any, params: Any) -> Any:
        return None

    assert ScenarioSpec("x", fn, True).manual is True
    assert ScenarioSpec("x", fn, True, manual=False).manual is False
    assert all(spec.manual for spec in SCENARIOS.values() if not spec.name.startswith("gadget_"))


@pytest.mark.parametrize("source", ["upgrades", "stocks"])
def test_refresh_gadget_sources(source: str) -> None:
    assert SCENARIOS["refresh"].invalid({"source": source}) == []


def test_gadget_scenarios_are_not_manual() -> None:
    assert GADGET_SCENARIOS <= SCENARIOS.keys()
    assert all(not SCENARIOS[name].manual for name in GADGET_SCENARIOS)
    assert GADGET_SCENARIOS <= CERTIFIED


def test_gadget_scenario_params() -> None:
    buy = {"rule": "empty", "slot": "right", "tier": 1, "price": 3, "reserve": 0}
    assert SCENARIOS["gadget_buy"].invalid(buy) == []
    bad = {"rule": "any", "slot": "ring", "tier": 15, "price": -1, "reserve": True}
    assert SCENARIOS["gadget_buy"].invalid(bad) == sorted(buy)
    assert SCENARIOS["gadget_wear_set"].invalid({"set": "um", "slots": ["right"]}) == []
    assert SCENARIOS["gadget_wear_set"].invalid({"set": "logistic"}) == ["set"]
    upgrade = {"task_id": 3, "slot": "pants", "target": 25, "kind": "auto", "batch": 20}
    assert SCENARIOS["gadget_upgrade"].invalid(upgrade) == []
    bad_upgrade = {"task_id": 0, "slot": "bag", "target": 61, "kind": "gold", "batch": 21}
    assert SCENARIOS["gadget_upgrade"].invalid(bad_upgrade) == sorted(upgrade)
