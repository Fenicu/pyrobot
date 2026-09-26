from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.engine.scenarios import library, obligations
from app.engine.scenarios.library import ScenarioFn


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    name: str
    fn: ScenarioFn
    certified: bool
    params: Mapping[str, Any] = field(default_factory=dict)


_CERTIFIED_DEEDS = frozenset({"harvest", "job", "learn", "dconv", "eat"})


def _specs() -> dict[str, ScenarioSpec]:
    specs = [
        ScenarioSpec("refresh", library.refresh, True),
        ScenarioSpec("fastfood", library.fastfood, True),
        ScenarioSpec("levelup", library.levelup, True),
        ScenarioSpec("gorbushka", library.gorbushka, True),
        ScenarioSpec("sleep", library.sleep, False),
        ScenarioSpec("battle_target", obligations.battle_target, True),
        ScenarioSpec("stocks_dump", obligations.stocks_dump, True),
        ScenarioSpec("factory_signup", obligations.factory_signup, True),
        ScenarioSpec("bulls_join", obligations.bulls_join, True),
        ScenarioSpec("tangerine", obligations.tangerine, True),
        ScenarioSpec("smoothie", obligations.smoothie, True),
    ]
    for item, certified in (
        ("book", True),
        ("card", True),
        ("prizebox", True),
        ("container_small", True),
        ("container_medium", False),
    ):
        specs.append(ScenarioSpec(item, library.free_item, certified, {"item": item}))
    for activity in library.DEED_COMMANDS:
        specs.append(
            ScenarioSpec(
                f"deed:{activity}",
                library.deed,
                activity in _CERTIFIED_DEEDS,
                {"activity": activity},
            )
        )
    return {s.name: s for s in specs}


SCENARIOS = _specs()
CERTIFIED = frozenset(name for name, spec in SCENARIOS.items() if spec.certified)
