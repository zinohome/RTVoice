"""Test 5 admin CLI commands using YAML store fixture."""
import pytest
from datetime import datetime, timezone


@pytest.fixture
def store(tmp_path):
    """fresh YAML store；返回 store + path."""
    from rtvoice_auth.store import YamlKeyStore
    p = tmp_path / "keys.yaml"
    s = YamlKeyStore(str(p))
    return s, str(p)


@pytest.mark.asyncio
async def test_cmd_create_returns_secret(store):
    from rtvoice_admin.commands import cmd_create
    s, _ = store
    await s.load()
    out = await cmd_create(s, name="cozyvoice",
                           sessions_concurrent=5, sessions_per_hour=200,
                           scopes=["stt", "tts", "realtime"])
    assert out["name"] == "cozyvoice"
    assert out["id"].startswith("key_")
    assert out["secret"]
    assert len(out["secret"]) >= 32


@pytest.mark.asyncio
async def test_cmd_list_excludes_secret(store):
    from rtvoice_admin.commands import cmd_create, cmd_list
    s, _ = store
    await s.load()
    await cmd_create(s, name="a", sessions_concurrent=1, sessions_per_hour=10,
                     scopes=["stt"])
    rows = await cmd_list(s)
    assert len(rows) == 1
    assert "secret" not in rows[0]
    assert rows[0]["name"] == "a"


@pytest.mark.asyncio
async def test_cmd_show_returns_detail(store):
    from rtvoice_admin.commands import cmd_create, cmd_show
    s, _ = store
    await s.load()
    out = await cmd_create(s, name="x", sessions_concurrent=2, sessions_per_hour=20,
                           scopes=["stt"])
    detail = await cmd_show(s, key_id=out["id"])
    assert detail is not None
    assert detail["name"] == "x"
    assert "secret" not in detail


@pytest.mark.asyncio
async def test_cmd_revoke_sets_revoked_at(store):
    from rtvoice_admin.commands import cmd_create, cmd_revoke
    s, _ = store
    await s.load()
    out = await cmd_create(s, name="x", sessions_concurrent=1, sessions_per_hour=10,
                           scopes=["stt"])
    ok = await cmd_revoke(s, key_id=out["id"])
    assert ok is True
    rec = s.find_by_id(out["id"])
    assert rec.revoked_at is not None


@pytest.mark.asyncio
async def test_cmd_rotate_returns_new_secret(store):
    from rtvoice_admin.commands import cmd_create, cmd_rotate
    s, _ = store
    await s.load()
    out = await cmd_create(s, name="x", sessions_concurrent=1, sessions_per_hour=10,
                           scopes=["stt"])
    old_hash = s.find_by_id(out["id"]).secret_hash
    new_out = await cmd_rotate(s, key_id=out["id"])
    assert new_out["secret"] != out["secret"]
    new_hash = s.find_by_id(out["id"]).secret_hash
    assert new_hash != old_hash
