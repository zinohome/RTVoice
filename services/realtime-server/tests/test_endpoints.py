"""Test FastAPI endpoints with TestClient: POST /v1/sessions + WS gateway."""
import json
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("RTVOICE_API_KEY", "")
    monkeypatch.setenv("RTVOICE_MAX_CONCURRENT_SESSIONS", "3")
    import importlib, sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_with_auth(monkeypatch):
    monkeypatch.setenv("RTVOICE_API_KEY", "test-key-32chars-test-key-32chars")
    monkeypatch.setenv("RTVOICE_MAX_CONCURRENT_SESSIONS", "3")
    import importlib, sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_info(client):
    r = client.get("/info")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "realtime-server"
    assert "version" in body
    assert "capabilities" in body
    assert body["capabilities"]["max_concurrent_sessions"] == 3


def test_openapi_paths_include_v1_sessions(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/v1/sessions" in paths


def test_create_session_default_voice(client):
    r = client.post("/v1/sessions", json={})
    assert r.status_code == 201
    body = r.json()
    assert body["session_id"].startswith("sess_")
    assert body["ws_url"].endswith(body["session_id"])
    assert body["voice"] == "default_zh_female"
    assert body["speed"] == 1.0
    assert "expires_at" in body


def test_create_session_custom_voice_speed(client):
    r = client.post("/v1/sessions", json={"voice": "alice", "speed": 1.5})
    assert r.status_code == 201
    body = r.json()
    assert body["voice"] == "alice"
    assert body["speed"] == 1.5


def test_create_session_speed_out_of_range_returns_422(client):
    r = client.post("/v1/sessions", json={"speed": 3.0})
    assert r.status_code == 422
    body = r.json()
    assert body["type"] == "error"
    assert body["code"] == "validation.invalid_request"


def test_create_session_capacity_full(client):
    """First 3 succeed, 4th returns 503 session.capacity_full."""
    for _ in range(3):
        r = client.post("/v1/sessions", json={})
        assert r.status_code == 201
    r = client.post("/v1/sessions", json={})
    assert r.status_code == 503
    body = r.json()
    assert body["type"] == "error"
    assert body["code"] == "session.capacity_full"


def test_create_session_auth_required(client_with_auth):
    """When RTVOICE_API_KEY set, missing Bearer returns 401."""
    r = client_with_auth.post("/v1/sessions", json={})
    assert r.status_code == 401
    body = r.json()
    assert body["code"] in ("auth.missing_token", "auth.invalid_token")


def test_create_session_auth_correct(client_with_auth):
    r = client_with_auth.post(
        "/v1/sessions",
        json={},
        headers={"Authorization": "Bearer test-key-32chars-test-key-32chars"},
    )
    assert r.status_code == 201


def test_ws_session_not_found(client):
    """Connect to non-existent session_id returns close 4404."""
    with pytest.raises(Exception):
        with client.websocket_connect("/v1/realtime/sess_nonexistent") as ws:
            ws.receive()


def test_ws_creator_binding_mismatch(client_with_auth):
    """Create session with key A, connect without bearer → close."""
    r = client_with_auth.post(
        "/v1/sessions",
        json={},
        headers={"Authorization": "Bearer test-key-32chars-test-key-32chars"},
    )
    sid = r.json()["session_id"]
    with pytest.raises(Exception):
        with client_with_auth.websocket_connect(f"/v1/realtime/{sid}") as ws:
            ws.receive_text()
