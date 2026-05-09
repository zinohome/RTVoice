"""Test Pydantic v2 request/response models + RealtimeEvent discriminated union."""
import pytest


def test_session_create_request_defaults():
    from rtvoice_client.models import SessionCreateRequest
    r = SessionCreateRequest()
    assert r.voice is None
    assert r.speed == 1.0
    assert r.prompt is None
    assert r.audit_persist is False


def test_session_create_request_speed_bounds():
    from rtvoice_client.models import SessionCreateRequest
    SessionCreateRequest(speed=0.5)
    SessionCreateRequest(speed=2.0)
    with pytest.raises(Exception):
        SessionCreateRequest(speed=3.0)
    with pytest.raises(Exception):
        SessionCreateRequest(speed=0.4)


def test_session_create_response_round_trip():
    from rtvoice_client.models import SessionCreateResponse
    data = {
        "session_id": "sess_x", "ws_url": "ws://...",
        "expires_at": "2026-05-09T16:00:00Z",
        "voice": "v", "speed": 1.0, "prompt": "p", "audit_persist": True,
    }
    r = SessionCreateResponse.model_validate(data)
    assert r.session_id == "sess_x"
    assert r.audit_persist is True


def test_realtime_event_discriminated_union():
    from rtvoice_client.models import parse_realtime_event, TranscriptFinal, ResponseDone
    assert isinstance(
        parse_realtime_event({"type": "transcript.final", "text": "hi"}),
        TranscriptFinal,
    )
    assert isinstance(
        parse_realtime_event({"type": "response.done", "text": "ok"}),
        ResponseDone,
    )


def test_realtime_event_unknown_type_returns_none():
    from rtvoice_client.models import parse_realtime_event
    assert parse_realtime_event({"type": "future.event", "x": 1}) is None


def test_response_pcm_holds_bytes():
    from rtvoice_client.models import ResponsePCM
    e = ResponsePCM(data=b"\x00\x01\x02")
    assert e.type == "response.pcm"
    assert e.data == b"\x00\x01\x02"
