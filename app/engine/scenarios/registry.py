from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from app.engine.gadget_catalog import BUYABLE_SETS, SHOP, SLOTS
from app.engine.gadgets import UPGRADE_BATCH
from app.engine.parsing.artifacts import RECOLLECTABLE
from app.engine.parsing.bulls import INVITE_CODE
from app.engine.parsing.smoothie import INGREDIENTS
from app.engine.parsing.trips import VEHICLES
from app.engine.scenarios import (
    artifacts,
    daily,
    gadgets,
    library,
    lottery,
    metro,
    obligations,
    trips,
)
from app.engine.scenarios.library import FOOD_BUTTONS, REFRESH, ScenarioFn
from app.engine.settings import Target

Check = Callable[[Any], bool]
ParamType = Literal["enum", "int", "string"]
TASK = re.compile(r"[A-Za-z]+_(?:easy|medium|hard)")


@dataclass(frozen=True, slots=True)
class Param:
    """Обязательный параметр: проверка — исходный предикат, остальное — описание для каталога
    (`values` у enum, `min`/`max` у int, `pattern` у string — без Python-специфики)."""

    check: Check
    type: ParamType
    values: tuple[str, ...] | None = None
    min: int | None = None
    max: int | None = None
    pattern: str | None = None

    def __call__(self, value: Any) -> bool:
        return self.check(value)

    def spec(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.values is not None:
            out["values"] = list(self.values)
        for name in ("min", "max", "pattern"):
            if (value := getattr(self, name)) is not None:
                out[name] = value
        return out


def portable(pattern: re.Pattern[str]) -> str:
    """Шаблон для OpenAPI: якоря строки в синтаксисе ECMA-262 (`^…$` вместо `\\A…\\Z`)."""
    source = pattern.pattern
    body = source.removeprefix("\\A").removesuffix("\\Z")
    return f"^{body}$"


def _one_of(values: Iterable[str]) -> Param:
    allowed = frozenset(values)
    return Param(
        lambda v: isinstance(v, str) and v in allowed, "enum", values=tuple(sorted(allowed))
    )


def _int(low: int | None = None, high: int | None = None) -> Param:
    def check(v: Any) -> bool:
        if not isinstance(v, int) or isinstance(v, bool):
            return False
        return (low is None or v >= low) and (high is None or v <= high)

    return Param(check, "int", min=low, max=high)


_invite = Param(
    lambda v: isinstance(v, str) and INVITE_CODE.match(v) is not None,
    "string",
    pattern=portable(INVITE_CODE),
)
_task = Param(
    lambda v: isinstance(v, str) and TASK.fullmatch(v) is not None,
    "string",
    pattern=portable(TASK),
)
_recipe = Param(
    lambda v: isinstance(v, str) and len(v) == 5 and all(fruit in INGREDIENTS for fruit in v),
    "string",
    # Альтернатива, а не класс символов: фрукты вне BMP, класс без флага `u` их не удержит.
    pattern="^(?:" + "|".join(INGREDIENTS) + "){5}$",
)


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    name: str
    fn: ScenarioFn
    certified: bool
    params: Mapping[str, Any] = field(default_factory=dict)
    # Параметры, без которых сценарий не исполнить: у решений планировщика они есть всегда,
    # ручной запуск без них отклоняется.
    required: Mapping[str, Param] = field(default_factory=dict)
    # False — запускает только планировщик: ручной запуск шёл бы от MANUAL, и его risky-шаги
    # требовали бы подтверждения на каждую команду.
    manual: bool = True

    def invalid(self, params: Mapping[str, Any]) -> list[str]:
        """Обязательные параметры, которых нет или значение которых недопустимо."""
        return sorted(k for k, ok in self.required.items() if k not in params or not ok(params[k]))


_CERTIFIED_DEEDS = frozenset(
    {"harvest", "job", "learn", "dconv", "eat", "walk", "confa", "startup"}
)


def _specs() -> dict[str, ScenarioSpec]:
    specs = [
        ScenarioSpec("refresh", library.refresh, True, required={"source": _one_of(REFRESH)}),
        ScenarioSpec("fastfood", library.fastfood, True, required={"food": _one_of(FOOD_BUTTONS)}),
        ScenarioSpec("levelup", library.levelup, True),
        ScenarioSpec("gorbushka", library.gorbushka, True),
        ScenarioSpec("sleep", library.sleep, True, required={"hours": _int(7, 12)}),
        ScenarioSpec(
            "battle_target",
            obligations.battle_target,
            True,
            required={"target": _one_of(get_args(Target))},
        ),
        ScenarioSpec(
            "stocks_dump",
            obligations.stocks_dump,
            True,
            required={"keep": _int(0), "margin": _int(0)},
        ),
        ScenarioSpec("factory_signup", obligations.factory_signup, True),
        ScenarioSpec("factory_report", obligations.factory_report, True),
        ScenarioSpec("bulls_join", obligations.bulls_join, True, required={"code": _invite}),
        ScenarioSpec(
            "tangerine",
            obligations.tangerine,
            True,
            required={"chat": _int(), "reply_to": _int()},
        ),
        ScenarioSpec("smoothie", obligations.smoothie, True, required={"recipe": _recipe}),
        ScenarioSpec("metro", metro.metro, True),
        ScenarioSpec("daily_refresh", daily.daily_refresh, True),
        ScenarioSpec("daily_pick", daily.daily_pick, True, required={"task": _task}),
        ScenarioSpec("team_pick", daily.team_pick, True, required={"task": _task}),
        # Параметры необязательные: ручной запуск без них берёт их у планировщика.
        ScenarioSpec("lottery_buy", lottery.lottery_buy, True),
        ScenarioSpec(
            "artifact_start",
            artifacts.artifact_start,
            True,
            required={"artifact": _one_of(RECOLLECTABLE)},
        ),
        ScenarioSpec("trip", trips.trip, True, required={"vehicle": _one_of(VEHICLES)}),
        ScenarioSpec("trips_refresh", trips.trips_refresh, True),
        # `wear` и `in_bag` необязательные: у `Param` нет типа bool.
        ScenarioSpec(
            "gadget_buy",
            gadgets.gadget_buy,
            True,
            manual=False,
            required={
                "rule": _one_of(("empty", "set", "replace")),
                "slot": _one_of(SHOP),
                "tier": _int(1, 14),
                "price": _int(0),
                "reserve": _int(0),
            },
        ),
        ScenarioSpec(
            "gadget_wear_set",
            gadgets.gadget_wear_set,
            True,
            manual=False,
            required={"set": _one_of(BUYABLE_SETS)},
        ),
        ScenarioSpec(
            "gadget_upgrade",
            gadgets.gadget_upgrade,
            True,
            manual=False,
            required={
                "task_id": _int(1),
                "slot": _one_of(SLOTS),
                "target": _int(1, 60),
                "kind": _one_of(("white", "blue", "red", "auto")),
                "batch": _int(1, UPGRADE_BATCH),
            },
        ),
    ]
    for item, certified in (
        ("book", True),
        ("card", True),
        ("prizebox", True),
        ("container_small", True),
        ("container_medium", True),
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
