"""Test QuotaTracker."""
import pytest
from datetime import datetime, timezone


def _make_key(concurrent=2, per_hour=5):
    from rtvoice_auth.models import Key
    return Key(id="k1", secret_hash="h", name="n",
               sessions_concurrent_max=concurrent,
               sessions_per_hour_max=per_hour,
               created_at=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_quota_acquire_under_concurrent_limit():
    from rtvoice_auth.quota import QuotaTracker
    q = QuotaTracker()
    k = _make_key(concurrent=3)
    await q.acquire_session(k)
    await q.acquire_session(k)


@pytest.mark.asyncio
async def test_quota_acquire_over_concurrent_raises():
    from rtvoice_auth.quota import QuotaTracker
    from rtvoice_auth.errors import QuotaExceeded
    q = QuotaTracker()
    k = _make_key(concurrent=2)
    await q.acquire_session(k)
    await q.acquire_session(k)
    with pytest.raises(QuotaExceeded) as exc:
        await q.acquire_session(k)
    assert "concurrent" in exc.value.code


@pytest.mark.asyncio
async def test_quota_release_decreases_concurrent():
    from rtvoice_auth.quota import QuotaTracker
    q = QuotaTracker()
    k = _make_key(concurrent=2)
    await q.acquire_session(k)
    await q.acquire_session(k)
    await q.release_session(k.id)
    await q.acquire_session(k)


@pytest.mark.asyncio
async def test_quota_per_hour_raises_after_limit():
    from rtvoice_auth.quota import QuotaTracker
    from rtvoice_auth.errors import QuotaExceeded
    q = QuotaTracker()
    k = _make_key(concurrent=100, per_hour=3)
    await q.acquire_session(k)
    await q.release_session(k.id)
    await q.acquire_session(k)
    await q.release_session(k.id)
    await q.acquire_session(k)
    await q.release_session(k.id)
    with pytest.raises(QuotaExceeded) as exc:
        await q.acquire_session(k)
    assert "per_hour" in exc.value.code


@pytest.mark.asyncio
async def test_quota_rollback_on_per_hour_failure():
    """超 per_hour 时 concurrent 不应被加（acquire 整体失败回滚）。"""
    from rtvoice_auth.quota import QuotaTracker
    from rtvoice_auth.errors import QuotaExceeded
    q = QuotaTracker()
    k = _make_key(concurrent=100, per_hour=1)
    await q.acquire_session(k)
    await q.release_session(k.id)
    with pytest.raises(QuotaExceeded):
        await q.acquire_session(k)
    assert q._concurrent.get("k1", 0) == 0


@pytest.mark.asyncio
async def test_quota_release_unknown_no_error():
    from rtvoice_auth.quota import QuotaTracker
    q = QuotaTracker()
    await q.release_session("unknown_key_id")
