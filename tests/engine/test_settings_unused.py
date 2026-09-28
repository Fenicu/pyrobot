"""Настройки, которые код не читает, помечены в схеме (`x-unused`) — и только они."""

import inspect
import re
from collections.abc import Iterator
from functools import cache
from pathlib import Path

from pydantic import BaseModel

from app.engine.commands import _FEATURE_CALLBACK, _FEATURE_TEXT
from app.engine.planner.base import FEATURE
from app.engine.settings import UNUSED, Settings

APP = Path(__file__).resolve().parents[2] / "app"
SETTINGS_PY = APP / "engine" / "settings.py"
# Так код обращается к секциям настроек: `self.cfg.metro`, `settings.engine`, `current.chats`.
SECTION_ACCESS = r"\b(?:cfg|settings|current|s)\.{}\b"


def leaves(model: type[BaseModel], prefix: str = "") -> Iterator[tuple[str, type[BaseModel]]]:
    """Листья настроек (путь через точку) и модель, в которой лист объявлен."""
    for name, field in model.model_fields.items():
        section = field.annotation
        if isinstance(section, type) and issubclass(section, BaseModel):
            yield from leaves(section, f"{prefix}{name}.")
        else:
            yield f"{prefix}{name}", model


def marked() -> set[str]:
    out = set()
    for path, model in leaves(Settings):
        extra = model.model_fields[path.rsplit(".", 1)[-1]].json_schema_extra
        if isinstance(extra, dict) and extra.get("x-unused"):
            out.add(path)
    return out


@cache
def sources() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(APP.rglob("*.py")) if p != SETTINGS_PY
    )


@cache
def features_read() -> frozenset[str]:
    """Флаги `features`, которые читают планировщик (`feature_on`) и шлюз (команды механик)."""
    names = {"deeds", *FEATURE.values()}
    names |= {f for _, f in _FEATURE_TEXT} | {f for _, f in _FEATURE_CALLBACK}
    names |= set(re.findall(r"\bfeatures\.(\w+)", sources()))
    return frozenset(names)


def aliases(model: type[BaseModel], leaf: str) -> set[str]:
    """Лист и свойства его секции, которые его читают (`metro.margin_min`)."""
    names = {leaf}
    for name, attr in vars(model).items():
        if isinstance(attr, property) and attr.fget is not None:
            if re.search(rf"\bself\.{leaf}\b", inspect.getsource(attr.fget)):
                names.add(name)
    return names


def mentioned(path: str, model: type[BaseModel]) -> bool:
    section, *_, leaf = path.split(".")
    if section == "features":
        return leaf in features_read()
    code = sources()
    if not re.search(SECTION_ACCESS.format(section), code):
        return False
    return any(re.search(rf"\.{n}\b|[\"']{n}[\"']", code) for n in aliases(model, leaf))


def test_unused_settings_are_exactly_the_unread_ones() -> None:
    unread = {path for path, model in leaves(Settings) if not mentioned(path, model)}
    assert marked() == unread == {"features.casino", "features.arena", "levelup.policy"}


def test_unused_mark_reaches_schema() -> None:
    schema = Settings.model_json_schema()["$defs"]
    features = schema["FeaturesSection"]["properties"]
    assert features["casino"]["x-unused"] is True and features["arena"]["x-unused"] is True
    assert "x-unused" not in features["lottery"]
    assert schema["LevelupSection"]["properties"]["policy"]["x-unused"] is True
    assert UNUSED == {"x-unused": True}
