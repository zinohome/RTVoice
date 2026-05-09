"""Test SP4-fix-1: Client (sync) 多次调用同 method 不抛 'Event loop is closed'."""
import respx


def test_client_multiple_sync_calls_share_loop():
    """同 Client 多次 create_session 不抛 RuntimeError: Event loop is closed."""
    from rtvoice_client import Client
    with respx.mock:
        respx.post("http://x:9000/v1/sessions").respond(
            201,
            json={
                "session_id": "sess_a", "ws_url": "ws://x:9000/v1/realtime/sess_a",
                "expires_at": "2026-05-09T16:00:00Z",
                "voice": "v", "speed": 1.0, "prompt": "p", "audit_persist": False,
            },
        )
        c = Client(base_url="http://x:9000")
        s1 = c.realtime.create_session()
        s2 = c.realtime.create_session()
        s3 = c.realtime.create_session()
        c.close()
    assert s1.session_id == "sess_a"
    assert s2.session_id == "sess_a"
    assert s3.session_id == "sess_a"


def test_client_close_idempotent():
    """close() 多次调用不抛."""
    from rtvoice_client import Client
    c = Client(base_url="http://x:9000")
    c.close()
    c.close()  # 第二次不抛


def test_client_context_manager():
    """with Client() as c: 自动 close."""
    from rtvoice_client import Client
    with Client(base_url="http://x:9000") as c:
        assert c.realtime is not None
    # 退出时 close()
