from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

MessageKind = Literal["new", "edit"]


@dataclass(frozen=True, slots=True)
class Button:
    text: str
    row: int
    col: int
    data: str | None = None
    url: str | None = None
    switch: str | None = None
    # Запрос кнопки инлайн-режима с выбором чата (`switch_inline_query_chosen_chat`) и текст
    # кнопки копирования (`copy_text`).
    switch_chosen: str | None = None
    copy: str | None = None

    def json(self) -> list[object]:
        """Кнопка в журнале и SSE; поля новых видов кнопок — только у них, чтобы хеши уже
        записанных сообщений не менялись."""
        out: list[object] = [self.text, self.row, self.col, self.data, self.url, self.switch]
        if self.switch_chosen is not None or self.copy is not None:
            out += [self.switch_chosen, self.copy]
        return out

    @classmethod
    def from_json(cls, b: list[Any]) -> Button:
        extra = b[6:8] if len(b) >= 8 else (None, None)
        return cls(b[0], b[1], b[2], b[3], b[4], b[5], extra[0], extra[1])


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
    created_at: datetime | None = None

    @property
    def origin(self) -> datetime:
        return self.created_at or self.date

    def button(self, data: str) -> Button | None:
        return next((b for b in self.inline if b.data == data), None)

    def markup_json(self) -> dict[str, object] | None:
        if self.inline:
            return {"inline": [b.json() for b in self.inline]}
        if self.reply_kb:
            return {"reply": [list(row) for row in self.reply_kb]}
        return None

    def content_hash(self) -> str:
        raw = json.dumps([self.text, self.markup_json()], ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode()).hexdigest()
