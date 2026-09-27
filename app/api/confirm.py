import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable, Sequence

# Токен подтверждения risky-команды: подпись над сессией, ключом идемпотентности, точными
# параметрами и версией состояния — подтверждение не переносится на другие параметры,
# другое состояние персонажа или другую сессию.
DEFAULT_TTL_S = 120.0


class ConfirmTokens:
    def __init__(
        self,
        secret: bytes | None = None,
        ttl_s: float = DEFAULT_TTL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._secret = secret or secrets.token_bytes(32)
        self.ttl_s = ttl_s
        self._clock = clock

    def issue(self, session_id: int, key: str, params: Sequence[object], version: int) -> str:
        expires = int(self._clock() + self.ttl_s)
        return f"{expires}.{self._sign(session_id, key, params, version, expires)}"

    def expires_at(self, token: str) -> int | None:
        head, _, _ = token.partition(".")
        return int(head) if head.isdigit() else None

    def check(
        self, token: str, session_id: int, key: str, params: Sequence[object], version: int
    ) -> str | None:
        """None — токен годен; иначе причина: `expired` или `invalid`."""
        expires = self.expires_at(token)
        if expires is None:
            return "invalid"
        sig = token.partition(".")[2]
        if not hmac.compare_digest(sig, self._sign(session_id, key, params, version, expires)):
            return "invalid"
        if self._clock() > expires:
            return "expired"
        return None

    def _sign(
        self, session_id: int, key: str, params: Sequence[object], version: int, expires: int
    ) -> str:
        raw = json.dumps([session_id, key, list(params), version, expires], ensure_ascii=False)
        digest = hmac.new(self._secret, raw.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")
