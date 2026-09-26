import json
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

from app.engine.gametime import from_msk_naive
from app.engine.types import Button, IncomingMessage

GAME_DIR = Path(__file__).parent / "game"


def _markup(raw: dict[str, Any] | None) -> tuple[tuple[Button, ...], tuple[tuple[str, ...], ...]]:
    if not raw:
        return (), ()
    inline = tuple(
        Button(
            text=b["text"],
            row=r,
            col=c,
            data=b.get("data"),
            url=b.get("url"),
            switch=b.get("switch"),
        )
        for r, row in enumerate(raw.get("inline") or [])
        for c, b in enumerate(row)
    )
    reply = tuple(tuple(row) for row in raw.get("reply") or [])
    return inline, reply


def record_message(rec: dict[str, Any]) -> IncomingMessage:
    edit = rec.get("edit_date")
    moment = from_msk_naive(datetime.fromisoformat(edit or rec["date"]))
    inline, reply = _markup(rec.get("markup"))
    return IncomingMessage(
        chat_id=rec["chat"],
        msg_id=rec["id"],
        revision=int(moment.timestamp()) if edit else 0,
        kind="edit" if edit else "new",
        date=moment,
        received_at=moment,
        text=rec.get("text"),
        inline=inline,
        reply_kb=reply,
        from_id=rec.get("from") or rec["chat"],
        created_at=from_msk_naive(datetime.fromisoformat(rec["date"])),
    )


@cache
def _family(family: str) -> tuple[IncomingMessage, ...]:
    path = GAME_DIR / f"{family}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    return tuple(record_message(json.loads(line)) for line in lines)


@cache
def game(family: str) -> dict[int, IncomingMessage]:
    """Сообщения семейства по id; у сохранённого с правками — последняя версия."""
    return {msg.msg_id: msg for msg in _family(family)}


def game_versions(family: str, msg_id: int) -> list[IncomingMessage]:
    return [msg for msg in _family(family) if msg.msg_id == msg_id]


def game_msg(family: str, msg_id: int, version: int | None = None) -> IncomingMessage:
    if version is None:
        return game(family)[msg_id]
    return game_versions(family, msg_id)[version]
