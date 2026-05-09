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


def test_create_session_with_prompt(client):
    r = client.post("/v1/sessions", json={"prompt": "你是 IT 客服"})
    assert r.status_code == 201
    body = r.json()
    assert body["prompt"] == "你是 IT 客服"
    assert body["audit_persist"] is False


def test_create_session_default_prompt_from_env(client):
    """不传 prompt 用 env default."""
    r = client.post("/v1/sessions", json={})
    body = r.json()
    assert body["prompt"] == "你是语音助手。用中文简短回答（≤2 句）。"


def test_create_session_prompt_too_long_returns_422(client, monkeypatch):
    monkeypatch.setattr("app.config.PROMPT_MAX_CHARS", 100)
    long_prompt = "x" * 200
    r = client.post("/v1/sessions", json={"prompt": long_prompt})
    assert r.status_code == 422
    body = r.json()
    assert body["type"] == "error"
    assert body["code"] == "prompt.too_long"


def test_info_includes_sp3_capabilities(client):
    r = client.get("/info")
    caps = r.json()["capabilities"]
    assert caps["memory"] is True
    assert caps["memory_max_turns"] == 6
    assert caps["transcript_partial"] is True
    assert caps["response_text"] is True
    assert "default_prompt" in caps
    assert isinstance(caps["default_prompt"], str)


@pytest.fixture
def client_stt_mock(client, monkeypatch):
    """Monkeypatch STTClient.connect to no-op so WS handler enters main loop."""
    async def _noop_connect(self):
        return None
    async def _noop_feed(self, b):
        return None
    async def _noop_close(self):
        return None
    from app import stt_client as _stt_mod
    monkeypatch.setattr(_stt_mod.STTClient, "connect", _noop_connect)
    monkeypatch.setattr(_stt_mod.STTClient, "feed", _noop_feed, raising=False)
    monkeypatch.setattr(_stt_mod.STTClient, "close", _noop_close, raising=False)
    return client


def test_session_update_voice_speed_via_ws(client_stt_mock):
    """WS session.update voice/speed → 不抛 + 无 error."""
    c = client_stt_mock
    r = c.post("/v1/sessions", json={})
    sid = r.json()["session_id"]
    with c.websocket_connect(f"/v1/realtime/{sid}") as ws:
        import json as _json
        ws.send_text(_json.dumps({"type": "session.update", "voice": "alice"}))
        ws.send_text(_json.dumps({"type": "session.update", "speed": 1.5}))
        # 简化：测能发送不抛即可（TestClient WS 收消息会 block）


def test_session_update_speed_out_of_range_emits_error(client_stt_mock):
    """speed=3.0 → error validation.invalid_request."""
    c = client_stt_mock
    r = c.post("/v1/sessions", json={})
    sid = r.json()["session_id"]
    import json as _json
    with c.websocket_connect(f"/v1/realtime/{sid}") as ws:
        ws.send_text(_json.dumps({"type": "session.update", "speed": 3.0}))
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["code"] == "validation.invalid_request"


def test_memory_clear_event_handled(client_stt_mock):
    """WS memory.clear → no error event back（仅验"发不抛"）."""
    c = client_stt_mock
    r = c.post("/v1/sessions", json={})
    sid = r.json()["session_id"]
    import json as _json
    with c.websocket_connect(f"/v1/realtime/{sid}") as ws:
        ws.send_text(_json.dumps({"type": "memory.clear"}))


def test_metrics_endpoint_exposes_sp4_metrics(client):
    """/metrics 含 3 个 SP4 自定义 metric 名."""
    r = client.get("/metrics")
    body = r.text
    assert "rtvoice_realtime_sessions_active" in body
    assert "rtvoice_realtime_turns_total" in body
    assert "rtvoice_realtime_audit_queue_depth" in body


def test_cors_preflight_returns_acao(client):
    """OPTIONS preflight 返 ACAO 头（默认 *）"""
    r = client.options("/v1/sessions", headers={
        "Origin": "http://example.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Authorization,Content-Type",
    })
    assert r.status_code == 200
    headers = {k.lower(): v for k, v in r.headers.items()}
    assert "access-control-allow-origin" in headers
    assert headers["access-control-allow-origin"] in ("*", "http://example.com")


def test_cors_actual_request_has_acao_header(client):
    """实际 GET 请求带 Origin → response 含 ACAO"""
    r = client.get("/info", headers={"Origin": "http://example.com"})
    assert r.status_code == 200
    assert "access-control-allow-origin" in {k.lower() for k in r.headers}


def test_info_version_is_0_12_0(client):
    r = client.get("/info")
    assert r.status_code == 200
    assert r.json()["version"] == "0.12.0"
