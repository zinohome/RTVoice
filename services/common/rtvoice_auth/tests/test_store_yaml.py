"""Test YAML KeyStore: CRUD + load + reload."""
import asyncio
import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_yaml_store_empty_load(tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    assert not s.any_keys()
    assert s.find_by_hash("anything") is None


@pytest.mark.asyncio
async def test_yaml_store_put_and_find(tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key
    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    k = Key(id="k1", secret_hash="abc", name="n", created_at=datetime.now(timezone.utc))
    await s.put(k)
    found = s.find_by_hash("abc")
    assert found is not None
    assert found.id == "k1"
    assert s.any_keys()


@pytest.mark.asyncio
async def test_yaml_store_persist_reload(tmp_path):
    """put 后 file 写入；新 store load 能读到."""
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key
    p = tmp_path / "keys.yaml"
    s1 = YamlKeyStore(str(p))
    await s1.load()
    await s1.put(Key(id="k1", secret_hash="h1", name="n", created_at=datetime.now(timezone.utc)))

    s2 = YamlKeyStore(str(p))
    await s2.load()
    assert s2.find_by_hash("h1") is not None


@pytest.mark.asyncio
async def test_yaml_store_revoke(tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key
    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    await s.put(Key(id="k1", secret_hash="h1", name="n", created_at=datetime.now(timezone.utc)))
    ok = await s.revoke("k1")
    assert ok is True
    found = s.find_by_hash("h1")
    assert found.revoked_at is not None


@pytest.mark.asyncio
async def test_yaml_store_revoke_unknown_returns_false(tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    ok = await s.revoke("nonexistent")
    assert ok is False


@pytest.mark.asyncio
async def test_yaml_store_list_all(tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key
    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    for i in range(3):
        await s.put(Key(id=f"k{i}", secret_hash=f"h{i}", name=f"n{i}",
                        created_at=datetime.now(timezone.utc)))
    keys = s.list_all()
    assert len(keys) == 3
    assert {k.id for k in keys} == {"k0", "k1", "k2"}


@pytest.mark.asyncio
async def test_yaml_store_reload_picks_up_new_key(tmp_path):
    """外部进程改 yaml 文件后 store.load() 能读到新 key（无需进程重启）."""
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key

    p = tmp_path / "keys.yaml"
    s1 = YamlKeyStore(str(p))
    await s1.load()
    s2 = YamlKeyStore(str(p))
    await s2.load()
    await s2.put(Key(id="kx", secret_hash="hx", name="x",
                     created_at=datetime.now(timezone.utc)))
    assert s1.find_by_hash("hx") is None
    await s1.load()
    assert s1.find_by_hash("hx") is not None


@pytest.mark.asyncio
async def test_yaml_store_reload_picks_up_revoke(tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key

    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    await s.put(Key(id="kr", secret_hash="hr", name="r",
                    created_at=datetime.now(timezone.utc)))
    s2 = YamlKeyStore(str(p))
    await s2.load()
    await s2.revoke("kr")
    await s.load()
    assert s.find_by_id("kr").revoked_at is not None
