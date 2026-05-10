"""verify_key + FastAPI require_key dependency."""
from __future__ import annotations
import hashlib
import hmac
import logging
from typing import Any

from rtvoice_auth.errors import InvalidToken, TokenRevoked, ScopeDenied
from rtvoice_auth.models import Key

log = logging.getLogger("rtvoice.auth.verify")


async def verify_key(secret: str, *, scope: str, store: Any) -> Key:
    """验证 plaintext secret；返 Key record，否则 raise AuthError 子类。

    步骤：
    1. provided_hash = sha256(secret)
    2. record = store.find_by_hash(provided_hash)
    3. None → InvalidToken
    4. revoked_at → TokenRevoked
    5. scope not in scopes → ScopeDenied
    """
    if not secret:
        raise InvalidToken("auth.invalid_token", "empty secret")
    provided_hash = hashlib.sha256(secret.encode()).hexdigest()
    record = store.find_by_hash(provided_hash)
    if record is None:
        raise InvalidToken("auth.invalid_token", "secret not recognized")
    # defensive：dict lookup 已 O(1) 命中即匹配；额外 hmac.compare_digest 防 timing
    if not hmac.compare_digest(record.secret_hash, provided_hash):
        raise InvalidToken("auth.invalid_token", "hash mismatch")
    if record.revoked_at is not None:
        raise TokenRevoked("auth.token_revoked", f"key {record.id} revoked at {record.revoked_at}")
    if scope and scope not in record.scopes:
        raise ScopeDenied("auth.scope_denied",
                          f"key {record.id} not allowed for scope={scope}")
    return record
