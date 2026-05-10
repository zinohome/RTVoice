"""Test init_key_store auto-migrate logic."""
import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_auto_migrate_when_empty_store_with_legacy(tmp_path, monkeypatch):
    """空 store + RTVOICE_API_KEY 设 → legacy-default 自动注册."""
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.lifespan import auto_migrate_legacy

    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    monkeypatch.setenv("RTVOICE_API_KEY", "legacy-secret-32-chars-test-test")
    migrated = await auto_migrate_legacy(s)
    assert migrated is not None
    assert migrated.legacy is True
    assert migrated.name == "legacy-default"
    assert s.any_keys()


@pytest.mark.asyncio
async def test_no_migrate_if_store_has_keys(tmp_path, monkeypatch):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.lifespan import auto_migrate_legacy
    from rtvoice_auth.models import Key

    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    await s.put(Key(id="existing", secret_hash="h", name="n",
                    created_at=datetime.now(timezone.utc)))
    monkeypatch.setenv("RTVOICE_API_KEY", "secret")
    migrated = await auto_migrate_legacy(s)
    assert migrated is None


@pytest.mark.asyncio
async def test_no_migrate_if_no_legacy_env(tmp_path, monkeypatch):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.lifespan import auto_migrate_legacy

    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    await s.load()
    monkeypatch.delenv("RTVOICE_API_KEY", raising=False)
    migrated = await auto_migrate_legacy(s)
    assert migrated is None
