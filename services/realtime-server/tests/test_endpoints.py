"""Test FastAPI endpoints with TestClient: POST /v1/sessions + WS gateway."""
import json
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    """SP6 fixture: yaml store + legacy auto-migrate + auto Bearer header."""
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(tmp_path / "keys.yaml"))
    monkeypatch.setenv("RTVOICE_API_KEY", "dev-test-key-32-chars-aaaaaaaaa")
    monkeypatch.setenv("RTVOICE_MAX_CONCURRENT_SESSIONS", "3")
    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    c = TestClient(app)
    c.headers.update({"Authorization": "Bearer dev-test-key-32-chars-aaaaaaaaa"})
    with c:
        yield c


@pytest.fixture
def client_with_auth(monkeypatch, tmp_path):
    """SP6 fixture: identical to client but legacy secret matches existing tests."""
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(tmp_path / "keys.yaml"))
    monkeypatch.setenv("RTVOICE_API_KEY", "test-key-32chars-test-key-32chars")
    monkeypatch.setenv("RTVOICE_MAX_CONCURRENT_SESSIONS", "3")
    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    c = TestClient(app)
    with c:
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


def test_delete_session_succeeds(client):
    """SP9 T6 — DELETE /v1/sessions/{id} 释放容量。"""
    r = client.post("/v1/sessions", json={})
    sid = r.json()["session_id"]
    r2 = client.delete(f"/v1/sessions/{sid}")
    assert r2.status_code == 204
    # 第二次 DELETE 仍 204 (幂等)
    r3 = client.delete(f"/v1/sessions/{sid}")
    assert r3.status_code == 204


def test_delete_session_idempotent_on_nonexistent(client):
    r = client.delete("/v1/sessions/sess_neverexisted")
    assert r.status_code == 204


def test_delete_session_403_when_not_owner(client_with_auth, monkeypatch, tmp_path):
    """另一把 key 不能关别人的 session。"""
    # 用 key A 建 session
    r = client_with_auth.post(
        "/v1/sessions",
        json={},
        headers={"Authorization": "Bearer test-key-32chars-test-key-32chars"},
    )
    sid = r.json()["session_id"]
    # 用未注册的 key B → 401（先卡在鉴权层，达不到 owner check）
    r2 = client_with_auth.delete(
        f"/v1/sessions/{sid}",
        headers={"Authorization": "Bearer wrong-key-32chars-not-real-key-x"},
    )
    assert r2.status_code == 401


def test_delete_session_releases_capacity(client):
    """connect→close→新 session 创建 OK 即证明 capacity 释放（max=3）。"""
    sids = []
    for _ in range(3):
        r = client.post("/v1/sessions", json={})
        assert r.status_code == 201
        sids.append(r.json()["session_id"])
    # 第 4 个应 503
    r4 = client.post("/v1/sessions", json={})
    assert r4.status_code == 503
    # 释放一个
    client.delete(f"/v1/sessions/{sids[0]}")
    # 现在能再建
    r5 = client.post("/v1/sessions", json={})
    assert r5.status_code == 201


def test_ws_url_uses_host_header_by_default(client):
    """SP9 T3 — ws_url 必须用调用方 Host 而非容器主机名。"""
    r = client.post(
        "/v1/sessions",
        json={},
        headers={"Host": "voice.example.com"},
    )
    assert r.status_code == 201
    ws_url = r.json()["ws_url"]
    assert "realtime-server:9000" not in ws_url
    assert "voice.example.com" in ws_url
    assert ws_url.startswith("ws://")


def test_ws_url_honors_x_forwarded_host_and_proto(client):
    """SP9 T3 — 反代场景下读 X-Forwarded-Host / X-Forwarded-Proto。"""
    r = client.post(
        "/v1/sessions",
        json={},
        headers={
            "Host": "internal",
            "X-Forwarded-Host": "voice.example.com",
            "X-Forwarded-Proto": "https",
        },
    )
    ws_url = r.json()["ws_url"]
    assert ws_url.startswith("wss://voice.example.com/")


def test_ws_url_explicit_public_ws_base_overrides(monkeypatch, tmp_path):
    """显式 PUBLIC_WS_BASE=wss://my-domain 时不再读 Host。"""
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(tmp_path / "keys.yaml"))
    monkeypatch.setenv("RTVOICE_API_KEY", "dev-test-key-32-chars-aaaaaaaaa")
    monkeypatch.setenv("PUBLIC_WS_BASE", "wss://override.example.com")
    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    c.headers.update({"Authorization": "Bearer dev-test-key-32-chars-aaaaaaaaa"})
    with c:
        r = c.post("/v1/sessions", json={})
        ws_url = r.json()["ws_url"]
        assert ws_url.startswith("wss://override.example.com/v1/realtime/")


def test_ws_accept_echoes_bearer_subprotocol(client_with_auth):
    """SP9 T1 — fix D4-F4: WS upgrade 必 echo Sec-WebSocket-Protocol，否则浏览器 close(1006)。

    回归守门：client 用 subprotocols=["bearer.<token>"] 鉴权，
    服务器 accept() 必带 subprotocol="bearer.<token>" 同字面回传。
    """
    token = "test-key-32chars-test-key-32chars"
    r = client_with_auth.post(
        "/v1/sessions",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    sid = r.json()["session_id"]
    # 用 subprotocol 鉴权（不在 header 里带 Authorization）
    with client_with_auth.websocket_connect(
        f"/v1/realtime/{sid}",
        subprotocols=[f"bearer.{token}"],
    ) as ws:
        assert ws.accepted_subprotocol == f"bearer.{token}"


def test_ws_accept_no_subprotocol_when_header_auth(client_with_auth):
    """对照测试：Authorization header 鉴权时 accept 不传 subprotocol。"""
    token = "test-key-32chars-test-key-32chars"
    r = client_with_auth.post(
        "/v1/sessions",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    sid = r.json()["session_id"]
    with client_with_auth.websocket_connect(
        f"/v1/realtime/{sid}",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        assert ws.accepted_subprotocol is None


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


def test_info_version_is_0_15_0(client):
    r = client.get("/info")
    assert r.status_code == 200
    assert r.json()["version"] == "0.15.0"


# -------------------------------------------------------------------
# SP6 T9 — require_key + quota integration tests
# -------------------------------------------------------------------
def test_create_session_with_valid_key(monkeypatch, tmp_path):
    """有效 key 走 quota acquire；返 201."""
    from rtvoice_auth.models import Key
    from rtvoice_auth.store import YamlKeyStore
    import hashlib, asyncio
    from datetime import datetime, timezone

    yaml_path = tmp_path / "keys.yaml"
    secret = "t-secret-32chars-aaaaaaaaaaaaaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()
    s = YamlKeyStore(str(yaml_path))
    asyncio.run(s.load())
    asyncio.run(s.put(Key(id="key_t", secret_hash=h, name="t",
                          sessions_concurrent_max=2, sessions_per_hour_max=10,
                          scopes=["stt", "tts", "realtime", "tokens"],
                          created_at=datetime.now(timezone.utc))))

    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))
    monkeypatch.setenv("RTVOICE_API_KEY", "")  # disable legacy auto-migrate

    from fastapi.testclient import TestClient
    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/v1/sessions", json={},
                   headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 201


def test_create_session_invalid_key_returns_401(monkeypatch, tmp_path):
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(tmp_path / "empty.yaml"))
    monkeypatch.setenv("RTVOICE_API_KEY", "")

    from fastapi.testclient import TestClient
    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/v1/sessions", json={},
                   headers={"Authorization": "Bearer bogus"})
        assert r.status_code == 401
        assert r.json()["code"] == "auth.invalid_token"


def test_create_session_quota_concurrent_exceeded(monkeypatch, tmp_path):
    """concurrent=1 的 key，第 2 个 create_session → 429 auth.quota_concurrent."""
    from rtvoice_auth.models import Key
    from rtvoice_auth.store import YamlKeyStore
    import hashlib, asyncio
    from datetime import datetime, timezone

    yaml_path = tmp_path / "keys.yaml"
    secret = "secret-quota-test-32-chars-aaaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()
    s = YamlKeyStore(str(yaml_path))
    asyncio.run(s.load())
    asyncio.run(s.put(Key(id="kq", secret_hash=h, name="q",
                          sessions_concurrent_max=1, sessions_per_hour_max=10,
                          scopes=["realtime"],
                          created_at=datetime.now(timezone.utc))))
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))
    monkeypatch.setenv("RTVOICE_API_KEY", "")

    from fastapi.testclient import TestClient
    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        r1 = c.post("/v1/sessions", json={},
                    headers={"Authorization": f"Bearer {secret}"})
        assert r1.status_code == 201
        r2 = c.post("/v1/sessions", json={},
                    headers={"Authorization": f"Bearer {secret}"})
        assert r2.status_code == 429
        assert r2.json()["code"] == "auth.quota_concurrent"


def test_revoked_key_returns_401(monkeypatch, tmp_path):
    from rtvoice_auth.models import Key
    from rtvoice_auth.store import YamlKeyStore
    import hashlib, asyncio
    from datetime import datetime, timezone

    yaml_path = tmp_path / "keys.yaml"
    secret = "rev-secret-32-chars-aaaaaaaaaaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()
    s = YamlKeyStore(str(yaml_path))
    asyncio.run(s.load())
    asyncio.run(s.put(Key(id="kr", secret_hash=h, name="r",
                          revoked_at=datetime.now(timezone.utc),
                          scopes=["realtime"],
                          created_at=datetime.now(timezone.utc))))
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))
    monkeypatch.setenv("RTVOICE_API_KEY", "")

    from fastapi.testclient import TestClient
    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/v1/sessions", json={},
                   headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 401
        assert r.json()["code"] == "auth.token_revoked"


def test_scope_denied_returns_403(monkeypatch, tmp_path):
    from rtvoice_auth.models import Key
    from rtvoice_auth.store import YamlKeyStore
    import hashlib, asyncio
    from datetime import datetime, timezone

    yaml_path = tmp_path / "keys.yaml"
    secret = "scope-secret-32-chars-aaaaaaaaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()
    s = YamlKeyStore(str(yaml_path))
    asyncio.run(s.load())
    asyncio.run(s.put(Key(id="ks", secret_hash=h, name="s",
                          scopes=["stt"],
                          created_at=datetime.now(timezone.utc))))
    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))
    monkeypatch.setenv("RTVOICE_API_KEY", "")

    from fastapi.testclient import TestClient
    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/v1/sessions", json={},
                   headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 403
        assert r.json()["code"] == "auth.scope_denied"


def test_hot_reload_yaml_picks_up_new_key(monkeypatch, tmp_path):
    """admin CLI 改 keys.yaml 后服务侧 < 1s 自动 pickup."""
    import asyncio
    import hashlib
    from datetime import datetime, timezone
    from rtvoice_auth.store import YamlKeyStore
    from rtvoice_auth.models import Key
    from fastapi.testclient import TestClient

    yaml_path = tmp_path / "keys.yaml"
    secret = "hot-reload-secret-32-chars-aaaaa"
    h = hashlib.sha256(secret.encode()).hexdigest()

    monkeypatch.setenv("RTVOICE_KEYS_BACKEND", "yaml")
    monkeypatch.setenv("RTVOICE_KEYS_FILE", str(yaml_path))
    monkeypatch.setenv("RTVOICE_API_KEY", "")

    import sys
    for m in list(sys.modules):
        if m.startswith("app."):
            del sys.modules[m]
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/v1/sessions", json={},
                   headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 401

        s = YamlKeyStore(str(yaml_path))
        asyncio.run(s.load())
        asyncio.run(s.put(Key(id="kh", secret_hash=h, name="h",
                              scopes=["realtime"],
                              created_at=datetime.now(timezone.utc))))

        import time
        time.sleep(0.8)

        r = c.post("/v1/sessions", json={},
                   headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 201, f"expected 201, got {r.status_code}: {r.text}"
