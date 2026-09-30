import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable, Sequence

# Токен подтверждения risky-команды: подпись над сессией, аккаунтом, ключом идемпотентности,
# точными параметрами и версией состояния — подтверждение не переносится на другие параметры,
# другое состояние персонажа, другой аккаунт или другую сессию.
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

    def issue(
        self,
        session_id: int,
        account_id: int,
        key: str,
        params: Sequence[object],
        version: int,
    ) -> str:
        expires = int(self._clock() + self.ttl_s)
        sig = self._sign(session_id, account_id, key, params, version, expires)
        return f"{expires}.{sig}"

    def expires_at(self, token: str) -> int | None:
        head, _, _ = token.partition(".")
        # isdigit без isascii пропускает «²» и цифры других алфавитов.
        return int(head) if head.isascii() and head.isdigit() else None

    def check(
        self,
        token: str,
        session_id: int,
        account_id: int,
        key: str,
        params: Sequence[object],
        version: int,
    ) -> str | None:
        """None — токен годен; иначе причина: `expired` или `invalid`."""
        expires = self.expires_at(token)
        # compare_digest не сравнивает строки с не-ASCII символами — падает, а не отвечает «нет».
        if expires is None or not token.isascii():
            return "invalid"
        sig = token.partition(".")[2]
        expected = self._sign(session_id, account_id, key, params, version, expires)
        if not hmac.compare_digest(sig, expected):
            return "invalid"
        if self._clock() > expires:
            return "expired"
        return None

    def _sign(
        self,
        session_id: int,
        account_id: int,
        key: str,
        params: Sequence[object],
        version: int,
        expires: int,
    ) -> str:
        raw = json.dumps(
            [session_id, account_id, key, list(params), version, expires], ensure_ascii=False
        )
        digest = hmac.new(self._secret, raw.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")
