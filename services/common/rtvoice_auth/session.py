"""Stateless signed session tokens for the Admin Console.

Admin 登录后下发一枚 HMAC-SHA256 签名的会话 token（放进 HttpOnly cookie）。
设计成无状态 + 用共享密钥（RTVOICE_SESSION_SECRET）签名，是为了让任意服务
（realtime / tts / stt / token）都能独立校验同一枚 cookie，而无需共享 session 存储。
这样 Admin UI 调各服务接口时只带 cookie、前端不碰任何 secret。

token 格式： base64url(payload_json) + "." + base64url(hmac_sha256(payload_json))
payload： {"sub": "<username>", "iat": <epoch>, "exp": <epoch>}
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any

log = logging.getLogger("rtvoice.auth.session")

COOKIE_NAME = "rtvoice_admin_session"
DEFAULT_TTL_SECONDS = 12 * 3600


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_session(username: str, secret: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """签发会话 token；调用方把它放进 HttpOnly cookie。"""
    now = int(time.time())
    payload = {"sub": username, "iat": now, "exp": now + ttl_seconds}
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"


def verify_session(token: str | None, secret: str) -> dict[str, Any] | None:
    """校验签名 + 过期；通过返 payload dict，否则返 None。"""
    if not token or "." not in token:
        return None
    try:
        payload_part, sig_part = token.split(".", 1)
        payload_bytes = _b64url_decode(payload_part)
        expected_sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
        provided_sig = _b64url_decode(sig_part)
        if not hmac.compare_digest(expected_sig, provided_sig):
            return None
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or time.time() >= exp:
        return None
    return payload
