"""Артефакты: экран «👾Артефакты», экран старта пересборки, «Сбор начат!» и строка части в
итоге дела."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar

from app.engine.events import Event
from app.engine.parsing.common import DURATION, dur
from app.engine.types import IncomingMessage

# Пересборка: ровно 10 суток с «Стартуем!»; уровень артефакта — до 100.
COLLECT_SPAN = timedelta(days=10)
MAX_LEVEL = 100
RECOLLECTABLE = ("book", "fax", "light")
DEEDS = {"🚶Прогулку": "walk", "💻Работу": "job", "📚Учёбу": "learn"}
# Имя артефакта в текстах плавает (регистр, пробел после значка, падеж): ключ — по слову.
_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("Буквар", "book"),
    ("факs", "fax"),
    ("Фонарь", "light"),
    ("грейд", "grade"),
    ("Идея", "idea"),
    ("жетон", "token"),
    ("план", "plan"),
    ("Диплом", "diploma"),
    ("Клава", "keyboard"),
    ("Фича", "feature"),
    ("Трёшка", "troika"),
    ("коробочка", "box"),
)
_SCREEN = re.compile(r"\A(?:👾Артефакты|Артефакты:)\n\nВсе артефакты в игре:")
_LEVEL = re.compile(r"^(?P<name>[^\n]+?) (?P<level>\d+) ур\.(?: t\.me/\S+)?$", re.M)
_RECOLLECT = re.compile(r"^[^\n]+ - /artr_(?P<key>book|fax|light)$", re.M)
_COLLECTING = re.compile(
    r"^Ты уже собираешь (?P<name>[^\n]+?)\. Окончание сборки через (?P<t>" + DURATION + r")$",
    re.M,
)
_START = re.compile(r"\AСтарт сбора артефакта (?P<name>[^\n]+)\n")
_STARTED = re.compile(
    r"\AСбор начат!\n\n\S+ (?P<name>[^\n]+?) выпадают при походах на "
    r"(?P<deed>🚶Прогулку|💻Работу|📚Учёбу)"
)
_PART = re.compile(
    r"^\S+[\xa0 ]\+1 ур\. артефакта (?P<name>[^\n]+?)\. Теперь (?P<level>\d+) ур\.$", re.M
)


def artifact_key(name: str) -> str | None:
    return next((key for word, key in _KEYWORDS if word in name), None)


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactsScreen(Event):
    """Уровни своих артефактов (у коробочки уровня нет), что можно пересобрать сейчас, идущий
    сбор и сколько до его конца."""

    kind: ClassVar[str] = "artifacts_screen"
    levels: dict[str, int] = field(default_factory=dict)
    recollect: tuple[str, ...] = ()
    collecting: str | None = None
    left_s: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactStartScreen(Event):
    kind: ClassVar[str] = "artifact_start_screen"
    artifact: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactCollectStarted(Event):
    """«Сбор начат!»: обнуляет уровень артефакта и 🔥 — итог, применяется один раз."""

    kind: ClassVar[str] = "artifact_collect_started"
    outcome: ClassVar[bool] = True
    artifact: str
    deed: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactPartFound(Event):
    kind: ClassVar[str] = "artifact_part_found"
    artifact: str
    level: int


def _screen(text: str) -> ArtifactsScreen:
    levels: dict[str, int] = {}
    for m in _LEVEL.finditer(text):
        if (key := artifact_key(m["name"])) is not None:
            levels[key] = int(m["level"])
    collect = _COLLECTING.search(text)
    return ArtifactsScreen(
        levels=levels,
        recollect=tuple(m["key"] for m in _RECOLLECT.finditer(text)),
        collecting=artifact_key(collect["name"]) if collect else None,
        left_s=dur(collect["t"]) if collect else None,
    )


def recognize_artifacts(msg: IncomingMessage) -> list[Event]:
    text = msg.text or ""
    if _SCREEN.match(text):
        return [_screen(text)]
    if m := _START.match(text):
        key = artifact_key(m["name"])
        return [ArtifactStartScreen(artifact=key)] if key is not None else []
    if m := _STARTED.match(text):
        key = artifact_key(m["name"])
        return [ArtifactCollectStarted(artifact=key, deed=DEEDS[m["deed"]])] if key else []
    return []


def recognize_part(msg: IncomingMessage) -> list[Event]:
    """Часть артефакта — строка в «Ты получил:» итога дела; итог дела классифицирует
    `activities`, здесь — только дополнительное событие."""
    m = _PART.search(msg.text or "")
    key = artifact_key(m["name"]) if m else None
    if m is None or key is None:
        return []
    return [ArtifactPartFound(artifact=key, level=int(m["level"]))]


RECOGNIZERS = (recognize_artifacts, recognize_part)
