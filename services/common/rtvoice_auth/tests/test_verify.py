"""Test verify_key + scope/revoked checks."""
from __future__ import annotations
import hashlib
import pytest
from datetime import datetime, timezone


def _make_store_with_key(scopes=None, revoked=False):
    """构造一个含单 key 的 in-memory store stub."""
    from rtvoice_auth.models import Key
    secret = "test-secret-32-chars-test-secret"
    h = hashlib.sha256(secret.encode()).hexdigest()
    k = Key(
        id="key_test", secret_hash=h, name="test",
        scopes=scopes or ["stt", "tts", "realtime", "tokens"],
        created_at=datetime.now(timezone.utc),
        revoked_at=datetime.now(timezone.utc) if revoked else None,
    )

    class _Store:
        def find_by_hash(self, h_):
            return k if h_ == h else None
    return _Store(), secret, k


@pytest.mark.asyncio
async def test_verify_valid_key():
    from rtvoice_auth.verify import verify_key
    store, secret, expected = _make_store_with_key()
    got = await verify_key(secret, scope="stt", store=store)
    assert got.id == expected.id


@pytest.mark.asyncio
async def test_verify_invalid_secret():
    from rtvoice_auth.verify import verify_key
    from rtvoice_auth.errors import InvalidToken
    store, _, _ = _make_store_with_key()
    with pytest.raises(InvalidToken):
        await verify_key("wrong-secret", scope="stt", store=store)


@pytest.mark.asyncio
async def test_verify_revoked():
    from rtvoice_auth.verify import verify_key
    from rtvoice_auth.errors import TokenRevoked
    store, secret, _ = _make_store_with_key(revoked=True)
    with pytest.raises(TokenRevoked):
        await verify_key(secret, scope="stt", store=store)


@pytest.mark.asyncio
async def test_verify_scope_denied():
    from rtvoice_auth.verify import verify_key
    from rtvoice_auth.errors import ScopeDenied
    store, secret, _ = _make_store_with_key(scopes=["stt"])
    with pytest.raises(ScopeDenied):
        await verify_key(secret, scope="realtime", store=store)


@pytest.mark.asyncio
async def test_verify_empty_secret():
    """Empty secret should raise InvalidToken."""
    from rtvoice_auth.verify import verify_key
    from rtvoice_auth.errors import InvalidToken
    store, _, _ = _make_store_with_key()
    with pytest.raises(InvalidToken):
        await verify_key("", scope="stt", store=store)


def test_verify_uses_constant_time_compare():
    """secret hash 比较使用 hmac.compare_digest，避免 timing attack。"""
    import inspect
    from rtvoice_auth import verify
    src = inspect.getsource(verify)
    assert "compare_digest" in src or "hmac" in src, "应该用 hmac.compare_digest 防 timing"
