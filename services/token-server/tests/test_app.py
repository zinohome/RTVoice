"""Test token-server with require_key + slowapi 共存."""
import asyncio, hashlib, pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient


def _reset_app_modules():
    """Drop cached app.* modules and unregister prometheus collectors so
    重新 import app.main 不会触发 'Duplicated timeseries' 注册冲突。"""
    import sys
    from prometheus_client import REGISTRY
    for collector in list(REGISTRY._collector_to_names.keys()):
        names = REGISTRY._collector_to_names.get(collector, [])
        if any(n.startswith("rtvoice_") for n in names):
            try:
                REGISTRY.unregister(collector)
            except Exception:
                pass
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]


@pytest.fixture
def client_with_key(monkeypatch, tmp_path):
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key

    yaml_path = tmp_path / "keys.yaml"
    secret = "tk-secret-32-chars-aaaaaaaaaaaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()
    s = YamlKeyStore(str(yaml_path))
    asyncio.run(s.load())
    asyncio.run(s.put(Key(id="ktk", secret_hash=h, name="tk",
                          scopes=["tokens"],
                          created_at=datetime.now(timezone.utc))))
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))
    monkeypatch.setenv("LIVEKIT_API_KEY", "dev-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "dev-secret-32-chars-aaaaaaaaa")
    monkeypatch.setenv("APP_API_KEY", "dev-test-key-32-chars-aaaaaaaaaaaa")  # legacy 防 startup 报错
    monkeypatch.setenv("RTVOICE_API_KEY", "")  # 不触发 legacy migrate

    _reset_app_modules()
    from app.main import app
    with TestClient(app) as c:
        yield c, secret


def test_token_endpoint_with_valid_key(client_with_key):
    c, secret = client_with_key
    r = c.post("/v1/tokens",
               json={"identity": "alice", "room": "test", "ttl_minutes": 5},
               headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 200
    assert "token" in r.json()


def test_token_endpoint_invalid_key(client_with_key):
    c, _ = client_with_key
    r = c.post("/v1/tokens",
               json={"identity": "alice", "room": "test"},
               headers={"Authorization": "Bearer bogus"})
    assert r.status_code == 401
    assert r.json()["code"] == "auth.invalid_token"


def test_token_endpoint_scope_denied(monkeypatch, tmp_path):
    """key 没 tokens scope → 403."""
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key

    yaml_path = tmp_path / "keys2.yaml"
    secret = "stt-only-secret-32-chars-aaaaaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()
    s = YamlKeyStore(str(yaml_path))
    asyncio.run(s.load())
    asyncio.run(s.put(Key(id="kstt", secret_hash=h, name="x",
                          scopes=["stt"],
                          created_at=datetime.now(timezone.utc))))
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("LIVEKIT_API_KEY", "dev-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "dev-secret-32-chars-aaaaaaaaa")
    monkeypatch.setenv("APP_API_KEY", "dev-test-key-32-chars-aaaaaaaaaaaa")
    monkeypatch.setenv("RTVOICE_API_KEY", "")

    _reset_app_modules()
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/v1/tokens",
                   json={"identity": "a", "room": "r"},
                   headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 403
        assert r.json()["code"] == "auth.scope_denied"
