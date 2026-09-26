from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

MessageKind = Literal["new", "edit"]


@dataclass(frozen=True, slots=True)
class Button:
    text: str
    row: int
    col: int
    data: str | None = None
    url: str | None = None
    switch: str | None = None


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    chat_id: int
    msg_id: int
    revision: int
    kind: MessageKind
    date: datetime
    received_at: datetime
    text: str | None
    inline: tuple[Button, ...] = ()
    reply_kb: tuple[tuple[str, ...], ...] = ()
    from_id: int | None = None
    outgoing: bool = False
    recovered: bool = False

    def button(self, data: str) -> Button | None:
        return next((b for b in self.inline if b.data == data), None)

    def markup_json(self) -> dict[str, object] | None:
        if self.inline:
            return {
                "inline": [[b.text, b.row, b.col, b.data, b.url, b.switch] for b in self.inline]
            }
        if self.reply_kb:
            return {"reply": [list(row) for row in self.reply_kb]}
        return None

    def content_hash(self) -> str:
        raw = json.dumps([self.text, self.markup_json()], ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode()).hexdigest()
