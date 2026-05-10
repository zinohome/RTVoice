"""Auth + quota typed exceptions."""
from __future__ import annotations


class AuthError(Exception):
    """所有 auth 错误的基类。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class InvalidToken(AuthError):
    """sha256 不匹配任何 key。"""


class TokenRevoked(AuthError):
    """key.revoked_at 已设。"""


class ScopeDenied(AuthError):
    """key.scopes 不含当前 service。"""


class QuotaExceeded(AuthError):
    """sessions_concurrent / sessions_per_hour 超限。"""
