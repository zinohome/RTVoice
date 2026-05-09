"""Typed exceptions matching RTVoice CONVENTIONS.md §6 error codes."""
from __future__ import annotations

from typing import Any


class RTVoiceError(Exception):
    """Base for all RTVoice client errors.

    Attributes:
        code: server-side error code (CONVENTIONS.md §6, e.g. 'session.capacity_full')
        message: human-readable message
        request_id: server-assigned request id (may be None)
        http_status: HTTP status code if from REST endpoint (None for WS errors)
    """

    def __init__(
        self,
        code: str,
        message: str,
        request_id: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.request_id = request_id
        self.http_status = http_status
        super().__init__(f"[{code}] {message}")


class AuthError(RTVoiceError):
    """auth.missing_token / auth.invalid_token (401)."""


class ValidationError(RTVoiceError):
    """validation.invalid_request (422) — body schema mismatch."""


class PromptTooLong(RTVoiceError):
    """prompt.too_long (422) — prompt > PROMPT_MAX_CHARS."""


class SessionError(RTVoiceError):
    """Session-scoped errors."""


class CapacityFull(SessionError):
    """session.capacity_full (503) — server reached MAX_CONCURRENT_SESSIONS."""


class SessionNotFound(SessionError):
    """session.not_found (WS close 4404)."""


class SessionExpired(SessionError):
    """session.expired (WS close 4410)."""


class SessionUnauthorized(SessionError):
    """session.unauthorized (WS close 4403) — Bearer mismatch with creator."""


class TurnError(RTVoiceError):
    """Turn-scoped errors."""


class TurnTimeout(TurnError):
    """turn.timeout."""


class TurnInProgress(TurnError):
    """turn.in_progress — sent audio.eos while previous turn not done."""


class STTError(RTVoiceError):
    """stt.empty / stt.timeout / stt.failed."""


class LLMError(RTVoiceError):
    """llm.failed."""


class TTSError(RTVoiceError):
    """tts.failed."""


class ServerError(RTVoiceError):
    """5xx / internal.unknown / unmapped error code."""


_CODE_MAP: dict[str, type[RTVoiceError]] = {
    "auth.missing_token": AuthError,
    "auth.invalid_token": AuthError,
    "validation.invalid_request": ValidationError,
    "prompt.too_long": PromptTooLong,
    "session.capacity_full": CapacityFull,
    "session.not_found": SessionNotFound,
    "session.expired": SessionExpired,
    "session.unauthorized": SessionUnauthorized,
    "turn.timeout": TurnTimeout,
    "turn.in_progress": TurnInProgress,
    "stt.empty": STTError,
    "stt.timeout": STTError,
    "stt.failed": STTError,
    "llm.failed": LLMError,
    "tts.failed": TTSError,
    "internal.unknown": ServerError,
    "internal.upstream_closed": ServerError,
}


def _code_to_class(code: str) -> type[RTVoiceError]:
    """Map error code → exception class. Unknown codes → ServerError."""
    return _CODE_MAP.get(code, ServerError)


def _raise_for_body(body: Any, http_status: int | None) -> None:
    """If body is RTVoice ErrorResponse shape, raise corresponding typed exception.

    Body is None or non-error → return without raising (5xx with None → ServerError)."""
    if body is None:
        if http_status is not None and http_status >= 500:
            raise ServerError(
                code="internal.unknown",
                message=f"server returned {http_status} with no body",
                http_status=http_status,
            )
        return
    if not isinstance(body, dict):
        return
    if body.get("type") != "error":
        return
    code = body.get("code", "internal.unknown")
    message = body.get("message", "")
    request_id = body.get("request_id")
    cls = _code_to_class(code)
    raise cls(code=code, message=message, request_id=request_id, http_status=http_status)
