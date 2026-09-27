import base64
import binascii
import json
from typing import Any

from fastapi import HTTPException, status


def encode_cursor(values: list[Any]) -> str:
    """Непрозрачный курсор страницы: JSON-список ключа последней записи в base64url."""
    raw = json.dumps(values, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, size: int) -> list[Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        values = json.loads(raw)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid cursor") from exc
    if not isinstance(values, list) or len(values) != size:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid cursor")
    return values
