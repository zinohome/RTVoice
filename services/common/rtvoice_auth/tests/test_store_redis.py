"""Test Redis KeyStore via fakeredis."""
import asyncio
import pytest
from datetime import datetime, timezone


@pytest.fixture
async def fake_redis():
    import fakeredis.aioredis
    r = fakeredis.aioredis.FakeRedis()
    yield r
    await r.aclose()


@pytest.mark.asyncio
async def test_redis_store_empty_load(fake_redis):
    from rtvoice_auth.store_redis import RedisKeyStore
    s = RedisKeyStore(fake_redis)
    await s.load()
    assert not s.any_keys()
    assert s.find_by_hash("nope") is None


@pytest.mark.asyncio
async def test_redis_store_put_and_find(fake_redis):
    from rtvoice_auth.store_redis import RedisKeyStore
    from rtvoice_auth.models import Key
    s = RedisKeyStore(fake_redis)
    await s.load()
    k = Key(id="k1", secret_hash="abc", name="n", created_at=datetime.now(timezone.utc))
    await s.put(k)
    found = s.find_by_hash("abc")
    assert found is not None
    assert found.id == "k1"


@pytest.mark.asyncio
async def test_redis_store_persist_across_instances(fake_redis):
    from rtvoice_auth.store_redis import RedisKeyStore
    from rtvoice_auth.models import Key
    s1 = RedisKeyStore(fake_redis)
    await s1.load()
    await s1.put(Key(id="k1", secret_hash="h1", name="n", created_at=datetime.now(timezone.utc)))
    s2 = RedisKeyStore(fake_redis)
    await s2.load()
    assert s2.find_by_hash("h1") is not None


@pytest.mark.asyncio
async def test_redis_store_revoke(fake_redis):
    from rtvoice_auth.store_redis import RedisKeyStore
    from rtvoice_auth.models import Key
    s = RedisKeyStore(fake_redis)
    await s.load()
    await s.put(Key(id="k1", secret_hash="h1", name="n", created_at=datetime.now(timezone.utc)))
    ok = await s.revoke("k1")
    assert ok is True
    await s.load()
    assert s.find_by_hash("h1").revoked_at is not None


@pytest.mark.asyncio
async def test_redis_store_list_all(fake_redis):
    from rtvoice_auth.store_redis import RedisKeyStore
    from rtvoice_auth.models import Key
    s = RedisKeyStore(fake_redis)
    await s.load()
    for i in range(3):
        await s.put(Key(id=f"k{i}", secret_hash=f"h{i}", name=f"n{i}",
                        created_at=datetime.now(timezone.utc)))
    keys = s.list_all()
    assert {k.id for k in keys} == {"k0", "k1", "k2"}
