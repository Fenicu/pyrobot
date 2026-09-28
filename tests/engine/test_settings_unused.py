"""Настройки, которые код не читает, помечены в схеме (`x-unused`) — и только они."""

import ast
import inspect
from collections.abc import Iterator
from functools import cache
from pathlib import Path

from pydantic import BaseModel

from app.engine.commands import _FEATURE_CALLBACK, _FEATURE_TEXT
from app.engine.planner.base import FEATURE
from app.engine.settings import UNUSED, Settings

APP = Path(__file__).resolve().parents[2] / "app"
SETTINGS_PY = APP / "engine" / "settings.py"
type Chain = tuple[str, ...]


def fields(model: type[BaseModel], prefix: Chain = ()) -> Iterator[tuple[Chain, type[BaseModel]]]:
    """Листья и группы настроек (путь по сегментам) с моделью, в которой поле объявлено."""
    for name, field in model.model_fields.items():
        yield (*prefix, name), model
        section = field.annotation
        if isinstance(section, type) and issubclass(section, BaseModel):
            yield from fields(section, (*prefix, name))


def leaves() -> Iterator[tuple[Chain, type[BaseModel]]]:
    for path, model in fields(Settings):
        section = model.model_fields[path[-1]].annotation
        if not (isinstance(section, type) and issubclass(section, BaseModel)):
            yield path, model


# Модель секции или группы → её путь: `cfg: MetroSection` или `MetroSection()` — это `metro`.
SECTIONS: dict[str, Chain] = {
    model.model_fields[path[-1]].annotation.__name__: path  # type: ignore[union-attr]
    for path, model in fields(Settings)
    if path not in {leaf for leaf, _ in leaves()}
}


def marked() -> set[str]:
    out = set()
    for path, model in leaves():
        extra = model.model_fields[path[-1]].json_schema_extra
        if isinstance(extra, dict) and extra.get("x-unused"):
            out.add(".".join(path))
    return out


def _bindings(scope: ast.AST) -> dict[str, ast.expr | Chain]:
    """Имена, связанные с выражением (`cfg = self.cfg.metro`) или секцией по аннотации."""
    env: dict[str, ast.expr | Chain] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            if isinstance(target := node.targets[0], ast.Name):
                env[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                env[node.target.id] = node.value
        elif isinstance(node, ast.arg) and node.annotation is not None:
            names = {n.id for n in ast.walk(node.annotation) if isinstance(n, ast.Name)}
            if section := next((SECTIONS[n] for n in names if n in SECTIONS), None):
                env[node.arg] = section
    return env


def _resolve(node: ast.expr, env: dict[str, ast.expr | Chain], depth: int = 0) -> Chain:
    if isinstance(node, ast.Attribute):
        return (*_resolve(node.value, env, depth), node.attr)
    if isinstance(node, ast.Name):
        bound = env.get(node.id)
        if isinstance(bound, tuple):
            return bound
        if bound is not None and depth < 8:
            return _resolve(bound, env, depth + 1)
        return (node.id,)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return SECTIONS.get(node.func.id, ("()",))
    return ("?",)


def chains(source: str) -> set[Chain]:
    """Цепочки обращений кода: `self.cfg.metro.buffs`, через локальные имена и аннотации
    раскрытые до пути настроек; `getattr(x, имя)` с вычисляемым именем — `x.*`."""
    tree = ast.parse(source)
    module = {k: v for k, v in _bindings(tree).items() if not isinstance(v, tuple)}
    scopes = [
        tree,
        *(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)),
    ]
    out: set[Chain] = set()
    for scope in scopes:
        env = {**module, **_bindings(scope)} if scope is not tree else module
        for node in ast.walk(scope):
            if isinstance(node, ast.Attribute):
                out.add(_resolve(node, env))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "getattr" and len(node.args) >= 2:
                    if not isinstance(node.args[1], ast.Constant):
                        out.add((*_resolve(node.args[0], env), "*"))
    return out


@cache
def app_chains() -> frozenset[Chain]:
    found: set[Chain] = set()
    for p in sorted(APP.rglob("*.py")):
        if p != SETTINGS_PY:
            found |= chains(p.read_text(encoding="utf-8"))
    return frozenset(found)


def features_read(found: frozenset[Chain] | set[Chain]) -> set[str]:
    """Флаги `features`: реестры механик планировщика (`feature_on`) и шлюза и прямые
    `features.<флаг>`; `getattr(features, флаг)` читает флаг из реестра, а не все подряд."""
    names = {"deeds", *FEATURE.values()}
    names |= {f for _, f in _FEATURE_TEXT} | {f for _, f in _FEATURE_CALLBACK}
    for chain in found:
        for i, part in enumerate(chain[:-1]):
            if part == "features" and chain[i + 1] != "*":
                names.add(chain[i + 1])
    return names


def aliases(model: type[BaseModel], leaf: str) -> set[str]:
    """Лист и свойства его секции, которые его читают (`metro.margin_min`)."""
    names = {leaf}
    for name, attr in vars(model).items():
        if isinstance(attr, property) and attr.fget is not None:
            if f"self.{leaf}" in inspect.getsource(attr.fget):
                names.add(name)
    return names


def _contains(chain: Chain, part: Chain) -> bool:
    n = len(part)
    return any(chain[i : i + n] == part for i in range(len(chain) - n + 1))


def is_read(path: Chain, model: type[BaseModel], found: frozenset[Chain] | set[Chain]) -> bool:
    if path[0] == "features":
        return path[1] in features_read(found)
    parent = path[:-1]
    wanted = [(*parent, name) for name in aliases(model, path[-1])] + [(*parent, "*")]
    return any(_contains(chain, part) for chain in found for part in wanted)


def unread(found: frozenset[Chain] | set[Chain]) -> set[str]:
    return {".".join(path) for path, model in leaves() if not is_read(path, model, found)}


def test_unused_settings_are_exactly_the_unread_ones() -> None:
    assert (
        marked() == unread(app_chains()) == {"features.casino", "features.arena", "levelup.policy"}
    )


def test_nested_leaf_counts_only_by_its_full_path() -> None:
    reserve = ("strategy", "reserve_ahead_min", "metro")
    model = type(Settings().strategy.reserve_ahead_min)
    # Другое `.metro` (секция метро, флаг) — не обращение к запасу под метро.
    decoy = chains(
        "x = self.cfg.metro.min_budget_min\ny = self.cfg.strategy.focus\nfeature_on('metro')"
    )
    assert not is_read(reserve, model, decoy)
    via_names = chains(
        "cfg = self.cfg.strategy\nahead = cfg.reserve_ahead_min\nprint(ahead.metro)"
    )
    assert is_read(reserve, model, via_names)
    via_annotation = chains("def f(a: ReserveAhead) -> int:\n    return a.metro")
    assert is_read(reserve, model, via_annotation)
    dynamic = chains("tickets = self.cfg.lottery.tickets\nx = getattr(tickets, c)")
    assert is_read(("lottery", "tickets", "raw"), type(Settings().lottery.tickets), dynamic)


def test_unused_mark_reaches_schema() -> None:
    schema = Settings.model_json_schema()["$defs"]
    features = schema["FeaturesSection"]["properties"]
    assert features["casino"]["x-unused"] is True and features["arena"]["x-unused"] is True
    assert "x-unused" not in features["lottery"]
    assert schema["LevelupSection"]["properties"]["policy"]["x-unused"] is True
    assert UNUSED == {"x-unused": True}
