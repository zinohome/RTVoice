"""Test RedisPubSubListener via fakeredis."""
import asyncio
import pytest


@pytest.fixture
async def fake_redis():
    import fakeredis.aioredis
    r = fakeredis.aioredis.FakeRedis()
    yield r
    await r.aclose()


@pytest.mark.asyncio
async def test_pubsub_listener_fires_on_publish(fake_redis):
    from rtvoice_auth.watcher import RedisPubSubListener
    fired = asyncio.Event()
    async def cb():
        fired.set()
    listener = RedisPubSubListener(fake_redis, on_change=cb, debounce_ms=50)
    await listener.start()
    await asyncio.sleep(0.1)
    await fake_redis.publish("rtvoice:keys:changed", "key_x")
    try:
        await asyncio.wait_for(fired.wait(), timeout=2.0)
    finally:
        await listener.stop()
    assert fired.is_set()


@pytest.mark.asyncio
async def test_pubsub_listener_debounces_rapid_publishes(fake_redis):
    from rtvoice_auth.watcher import RedisPubSubListener
    calls = []
    async def cb():
        calls.append(1)
    listener = RedisPubSubListener(fake_redis, on_change=cb, debounce_ms=100)
    await listener.start()
    await asyncio.sleep(0.1)
    for i in range(3):
        await fake_redis.publish("rtvoice:keys:changed", f"k{i}")
    await asyncio.sleep(0.3)
    await listener.stop()
    assert len(calls) == 1, f"expected 1 reload after debounce, got {len(calls)}"


@pytest.mark.asyncio
async def test_pubsub_listener_stop_cleanly(fake_redis):
    from rtvoice_auth.watcher import RedisPubSubListener
    async def cb():
        pass
    listener = RedisPubSubListener(fake_redis, on_change=cb, debounce_ms=50)
    await listener.start()
    await asyncio.sleep(0.05)
    await listener.stop()
