"""Test Realtime namespace: primitives + conversation helper."""
import asyncio
import json
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_create_session_returns_typed_response():
    from rtvoice_client.realtime import AsyncRealtime
    from rtvoice_client.models import SessionCreateResponse
    with respx.mock:
        respx.post("http://rt:9000/v1/sessions").respond(
            201,
            json={
                "session_id": "sess_a", "ws_url": "ws://rt:9000/v1/realtime/sess_a",
                "expires_at": "2026-05-09T16:00:00Z", "voice": "v",
                "speed": 1.0, "prompt": "p", "audit_persist": False,
            },
        )
        async with httpx.AsyncClient() as h:
            rt = AsyncRealtime(h, "http://rt:9000", api_key=None)
            resp = await rt.create_session(prompt="p")
        assert isinstance(resp, SessionCreateResponse)
        assert resp.session_id == "sess_a"


@pytest.mark.asyncio
async def test_create_session_capacity_full_raises():
    from rtvoice_client.realtime import AsyncRealtime
    from rtvoice_client.errors import CapacityFull
    with respx.mock:
        respx.post("http://rt:9000/v1/sessions").respond(
            503,
            json={"type": "error", "code": "session.capacity_full",
                  "message": "max 5", "request_id": "r"},
        )
        async with httpx.AsyncClient() as h:
            rt = AsyncRealtime(h, "http://rt:9000", api_key=None)
            with pytest.raises(CapacityFull):
                await rt.create_session()


@pytest.mark.asyncio
async def test_prompt_too_long_raises():
    from rtvoice_client.realtime import AsyncRealtime
    from rtvoice_client.errors import PromptTooLong
    with respx.mock:
        respx.post("http://rt:9000/v1/sessions").respond(
            422,
            json={"type": "error", "code": "prompt.too_long",
                  "message": "x", "request_id": "r"},
        )
        async with httpx.AsyncClient() as h:
            rt = AsyncRealtime(h, "http://rt:9000", api_key=None)
            with pytest.raises(PromptTooLong):
                await rt.create_session(prompt="x" * 9999)


@pytest.mark.asyncio
async def test_realtime_session_update_methods_send_correct_json():
    """Session has update_prompt/update_voice/update_speed/clear_memory."""
    from rtvoice_client.realtime import AsyncRealtimeSession
    fake_ws = AsyncMock()
    fake_ws.send = AsyncMock()
    sess = AsyncRealtimeSession(fake_ws)
    await sess.update_prompt("hello")
    fake_ws.send.assert_awaited_with(json.dumps({"type": "session.update", "prompt": "hello"}))
    await sess.update_voice("alice")
    fake_ws.send.assert_awaited_with(json.dumps({"type": "session.update", "voice": "alice"}))
    await sess.update_speed(1.5)
    fake_ws.send.assert_awaited_with(json.dumps({"type": "session.update", "speed": 1.5}))
    await sess.clear_memory()
    fake_ws.send.assert_awaited_with(json.dumps({"type": "memory.clear"}))


@pytest.mark.asyncio
async def test_realtime_session_eos_sends_audio_eos():
    from rtvoice_client.realtime import AsyncRealtimeSession
    fake_ws = AsyncMock()
    fake_ws.send = AsyncMock()
    sess = AsyncRealtimeSession(fake_ws)
    await sess.eos()
    fake_ws.send.assert_awaited_with("audio.eos")


@pytest.mark.asyncio
async def test_realtime_events_parses_typed():
    """Iterate ws.recv → typed RealtimeEvent."""
    from rtvoice_client.realtime import AsyncRealtimeSession
    msgs = [
        json.dumps({"type": "transcript.final", "text": "hi"}),
        b"\x00\x01\x02",
        json.dumps({"type": "response.done", "text": "ok"}),
    ]
    fake_ws = AsyncMock()
    fake_ws.recv = AsyncMock(side_effect=msgs + [Exception("end")])
    sess = AsyncRealtimeSession(fake_ws)
    collected = []
    try:
        async for evt in sess.events():
            collected.append(evt)
            if hasattr(evt, "type") and evt.type == "response.done":
                break
    except Exception:
        pass
    types = [type(e).__name__ for e in collected]
    assert "TranscriptFinal" in types
    assert "ResponsePCM" in types
    assert "ResponseDone" in types


@pytest.mark.asyncio
async def test_conversation_helper_full_flow():
    """conversation() creates session, connects, feeds audio, yields events."""
    from contextlib import asynccontextmanager
    from rtvoice_client.realtime import AsyncRealtime, AsyncRealtimeSession
    from rtvoice_client.models import SessionCreateResponse, ResponseDone

    fake_ws = AsyncMock()
    fake_ws.send = AsyncMock()
    msg_iter = iter([
        json.dumps({"type": "transcript.final", "text": "hi"}),
        json.dumps({"type": "response.done", "text": "ok"}),
    ])
    async def _recv():
        return next(msg_iter)
    fake_ws.recv = _recv

    rt = AsyncRealtime.__new__(AsyncRealtime)
    rt._http = MagicMock()
    rt._base = "http://rt:9000"
    rt._api_key = None

    async def _create(**kwargs):
        return SessionCreateResponse(
            session_id="sess_x", ws_url="ws://rt/sess_x",
            expires_at="2026-05-09T16:00:00Z",
            voice="v", speed=1.0, prompt="p", audit_persist=False,
        )
    rt.create_session = _create

    @asynccontextmanager
    async def _connect(sess):
        yield AsyncRealtimeSession(fake_ws)
    rt.connect = _connect

    async def _audio_iter():
        yield b"\x00" * 100

    events = []
    async for evt in rt.conversation(_audio_iter(), prompt="p"):
        events.append(evt)
        if isinstance(evt, ResponseDone):
            break
    types = [type(e).__name__ for e in events]
    assert "TranscriptFinal" in types
    assert "ResponseDone" in types


def test_sync_realtime_create_session_via_run():
    from rtvoice_client.realtime import SyncRealtime, AsyncRealtime
    from rtvoice_client.models import SessionCreateResponse
    inner = MagicMock(spec=AsyncRealtime)
    inner.create_session = AsyncMock(return_value=SessionCreateResponse(
        session_id="sess_x", ws_url="ws://x", expires_at="t",
        voice="v", speed=1.0, prompt="p", audit_persist=False,
    ))
    sync = SyncRealtime(inner)
    r = sync.create_session(prompt="p")
    assert r.session_id == "sess_x"
