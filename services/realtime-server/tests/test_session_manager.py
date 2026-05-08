"""Test SessionManager: create / get / cleanup / capacity / expire."""
import asyncio
from datetime import datetime, timedelta, timezone
import pytest


@pytest.fixture
def mgr(monkeypatch):
    from app import config, session_manager
    monkeypatch.setattr(config, "MAX_CONCURRENT_SESSIONS", 3)
    monkeypatch.setattr(config, "SESSION_MAX_LIFETIME_S", 60)
    monkeypatch.setattr(config, "SESSION_IDLE_TIMEOUT_S", 10)
    monkeypatch.setattr(config, "SESSION_CREATE_TIMEOUT_S", 60)
    return session_manager.SessionManager()


@pytest.mark.asyncio
async def test_create_returns_session_with_stripe_id(mgr):
    """session_id 是 sess_<urlsafe-12bytes>"""
    sess = await mgr.create(creator_key_hash="hash1", voice="alice", speed=1.0)
    assert sess.id.startswith("sess_")
    assert len(sess.id) >= 17  # "sess_" + 12 chars+
    assert sess.creator_key_hash == "hash1"
    assert sess.voice == "alice"
    assert sess.speed == 1.0
    assert sess.state == "CREATED"
    assert sess.expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_get_returns_existing_session(mgr):
    sess = await mgr.create("h", "v", 1.0)
    got = mgr.get(sess.id)
    assert got is sess


def test_get_returns_none_when_not_found(mgr):
    assert mgr.get("sess_nonexistent") is None


@pytest.mark.asyncio
async def test_capacity_full_raises(mgr):
    """When MAX_CONCURRENT_SESSIONS reached, create raises CapacityFull."""
    from app.session_manager import CapacityFull
    for _ in range(3):  # cap = 3 from fixture
        await mgr.create("h", "v", 1.0)
    with pytest.raises(CapacityFull):
        await mgr.create("h", "v", 1.0)


@pytest.mark.asyncio
async def test_active_count(mgr):
    assert mgr.active_count() == 0
    await mgr.create("h", "v", 1.0)
    assert mgr.active_count() == 1


@pytest.mark.asyncio
async def test_cleanup_removes_session(mgr):
    sess = await mgr.create("h", "v", 1.0)
    await mgr.cleanup(sess.id, reason="test")
    assert mgr.get(sess.id) is None
    assert mgr.active_count() == 0


@pytest.mark.asyncio
async def test_cleanup_idempotent(mgr):
    """cleanup of already-cleaned session is no-op (no exception)."""
    sess = await mgr.create("h", "v", 1.0)
    await mgr.cleanup(sess.id, reason="test1")
    await mgr.cleanup(sess.id, reason="test2")  # should not raise
    assert mgr.active_count() == 0


@pytest.mark.asyncio
async def test_expire_loop_removes_expired(mgr, monkeypatch):
    """Background expire loop removes sessions past expires_at."""
    sess = await mgr.create("h", "v", 1.0)
    sess.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await mgr._expire_pass()
    assert mgr.get(sess.id) is None


@pytest.mark.asyncio
async def test_expire_loop_removes_idle(mgr):
    """Background expire loop removes sessions with last_activity > IDLE_TIMEOUT ago."""
    sess = await mgr.create("h", "v", 1.0)
    sess.last_activity = datetime.now(timezone.utc) - timedelta(seconds=11)
    sess.state = "ACTIVE"
    await mgr._expire_pass()
    assert mgr.get(sess.id) is None


@pytest.mark.asyncio
async def test_attach_ws_transitions_state(mgr):
    """attach_ws moves CREATED → ACTIVE."""
    sess = await mgr.create("h", "v", 1.0)
    assert sess.state == "CREATED"
    fake_ws = object()
    ok = mgr.attach_ws(sess.id, fake_ws)
    assert ok is True
    assert sess.state == "ACTIVE"
    assert sess.ws is fake_ws


@pytest.mark.asyncio
async def test_attach_ws_fails_if_not_created(mgr):
    """attach_ws returns False if session already cleanup'd."""
    sess = await mgr.create("h", "v", 1.0)
    await mgr.cleanup(sess.id, "test")
    ok = mgr.attach_ws(sess.id, object())
    assert ok is False
