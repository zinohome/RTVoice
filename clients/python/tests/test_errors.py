"""Test typed exception hierarchy + code → class 映射."""
import pytest


def test_base_class_carries_metadata():
    from rtvoice_client.errors import RTVoiceError
    exc = RTVoiceError(code="x.y", message="oops", request_id="req_1", http_status=500)
    assert exc.code == "x.y"
    assert exc.message == "oops"
    assert exc.request_id == "req_1"
    assert exc.http_status == 500
    assert "x.y" in str(exc)


def test_all_subclasses_inherit_base():
    from rtvoice_client.errors import (
        RTVoiceError, AuthError, ValidationError, PromptTooLong,
        CapacityFull, SessionNotFound, SessionExpired, SessionUnauthorized,
        TurnTimeout, TurnInProgress, STTError, LLMError, TTSError, ServerError,
    )
    for cls in (AuthError, ValidationError, PromptTooLong, CapacityFull,
                SessionNotFound, SessionExpired, SessionUnauthorized,
                TurnTimeout, TurnInProgress, STTError, LLMError, TTSError,
                ServerError):
        assert issubclass(cls, RTVoiceError)


@pytest.mark.parametrize("code,expected_cls_name", [
    ("auth.missing_token", "AuthError"),
    ("auth.invalid_token", "AuthError"),
    ("validation.invalid_request", "ValidationError"),
    ("prompt.too_long", "PromptTooLong"),
    ("session.capacity_full", "CapacityFull"),
    ("session.not_found", "SessionNotFound"),
    ("session.expired", "SessionExpired"),
    ("session.unauthorized", "SessionUnauthorized"),
    ("turn.timeout", "TurnTimeout"),
    ("turn.in_progress", "TurnInProgress"),
    ("stt.empty", "STTError"),
    ("stt.timeout", "STTError"),
    ("stt.failed", "STTError"),
    ("llm.failed", "LLMError"),
    ("tts.failed", "TTSError"),
    ("internal.unknown", "ServerError"),
])
def test_raise_for_code_maps_correctly(code, expected_cls_name):
    from rtvoice_client import errors
    body = {"type": "error", "code": code, "message": "msg", "request_id": "req"}
    cls = errors._code_to_class(code)
    assert cls.__name__ == expected_cls_name
    with pytest.raises(getattr(errors, expected_cls_name)):
        errors._raise_for_body(body, http_status=500)


def test_unknown_code_falls_back_to_server_error():
    from rtvoice_client import errors
    body = {"type": "error", "code": "future.unknown_thing", "message": "x"}
    with pytest.raises(errors.ServerError):
        errors._raise_for_body(body, http_status=500)


def test_raise_for_response_handles_non_error_body():
    """Non-error JSON body → no raise."""
    from rtvoice_client import errors
    errors._raise_for_body({"foo": "bar"}, http_status=200)


def test_raise_for_response_handles_5xx_no_body():
    """5xx with no JSON body → ServerError."""
    from rtvoice_client import errors
    with pytest.raises(errors.ServerError):
        errors._raise_for_body(None, http_status=500)
