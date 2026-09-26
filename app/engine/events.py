from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, ClassVar


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    kind: ClassVar[str] = "event"

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, **asdict(self)}


@dataclass(frozen=True, slots=True, kw_only=True)
class AntiFlood(Event):
    kind: ClassVar[str] = "antiflood"
