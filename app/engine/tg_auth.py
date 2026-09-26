from __future__ import annotations


class TgAuthError(Exception):
    pass


class PasswordRequired(TgAuthError):
    pass


class InvalidCode(TgAuthError):
    pass


class CodeExpired(TgAuthError):
    pass


class InvalidPassword(TgAuthError):
    pass


class SignUpRequired(TgAuthError):
    pass
