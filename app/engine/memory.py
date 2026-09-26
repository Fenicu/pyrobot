from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.engine.events import Event
from app.engine.types import IncomingMessage


class MemoryJournal:
    def __init__(self) -> None:
        self.rows: list[tuple[IncomingMessage, list[Event]]] = []
        self.snapshot: tuple[dict[str, Any], int] = ({}, 0)
        self._keys: set[tuple[int, int, int, str]] = set()

    async def load_state(self) -> tuple[dict[str, Any], int]:
        return self.snapshot

    async def append(
        self,
        msg: IncomingMessage,
        events: Sequence[Event],
        new_state: dict[str, Any] | None,
        new_version: int,
    ) -> int | None:
        key = (msg.chat_id, msg.msg_id, msg.revision, msg.content_hash())
        if key in self._keys:
            return None
        self._keys.add(key)
        self.rows.append((msg, list(events)))
        if new_state is not None:
            self.snapshot = (new_state, new_version)
        return len(self.rows)
