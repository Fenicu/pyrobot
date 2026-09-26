from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, ClassVar


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    kind: ClassVar[str] = "event"
    # Итог (награда, старт дела, трата) применяется к состоянию один раз на сообщение,
    # даже если игра его правит.
    outcome: ClassVar[bool] = False

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, **asdict(self)}


@dataclass(frozen=True, slots=True, kw_only=True)
class AntiFlood(Event):
    kind: ClassVar[str] = "antiflood"


@dataclass(frozen=True, slots=True, kw_only=True)
class Unrecognized(Event):
    kind: ClassVar[str] = "unrecognized"
    first_line: str
