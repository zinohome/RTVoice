"""Pydantic v2 models for RTVoice API requests/responses + WS events."""
from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class SessionCreateRequest(BaseModel):
    """Request to create a realtime session."""

    voice: str | None = None
    speed: float = Field(1.0, ge=0.5, le=2.0)
    prompt: str | None = None
    audit_persist: bool = False


class SessionCreateResponse(BaseModel):
    """Response from session creation, includes WS endpoint."""

    session_id: str
    ws_url: str
    expires_at: str
    voice: str
    speed: float
    prompt: str
    audit_persist: bool


class TokenRequest(BaseModel):
    """Request to generate LiveKit access token."""

    identity: str
    room: str
    ttl_minutes: int = 10


class TokenResponse(BaseModel):
    """LiveKit token + connection details."""

    token: str
    url: str
    room: str
    identity: str


# ============ Realtime WS events ============


class TranscriptPartial(BaseModel):
    """Incremental transcript from STT (not final)."""

    type: Literal["transcript.partial"] = "transcript.partial"
    text: str
    stable: bool = False


class TranscriptFinal(BaseModel):
    """Final transcript segment from STT."""

    type: Literal["transcript.final"] = "transcript.final"
    text: str


class ResponseText(BaseModel):
    """LLM-generated text response."""

    type: Literal["response.text"] = "response.text"
    text: str


class ResponseDone(BaseModel):
    """Signal that response generation is complete."""

    type: Literal["response.done"] = "response.done"
    text: str = ""


class ResponsePCM(BaseModel):
    """Synthetic event: SDK wraps binary WS frame as typed event."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    type: Literal["response.pcm"] = "response.pcm"
    data: bytes


class ErrorEvent(BaseModel):
    """Error event from server."""

    type: Literal["error"] = "error"
    code: str
    message: str
    request_id: str | None = None


RealtimeEvent = Union[
    TranscriptPartial,
    TranscriptFinal,
    ResponseText,
    ResponseDone,
    ResponsePCM,
    ErrorEvent,
]

_EVENT_MAP: dict[str, type[BaseModel]] = {
    "transcript.partial": TranscriptPartial,
    "transcript.final": TranscriptFinal,
    "response.text": ResponseText,
    "response.done": ResponseDone,
    "error": ErrorEvent,
}


def parse_realtime_event(payload: dict[str, Any]) -> RealtimeEvent | None:
    """Parse server-sent JSON event into typed model. Unknown type → None."""
    t = payload.get("type")
    cls = _EVENT_MAP.get(t)
    if cls is None:
        return None
    return cls.model_validate(payload)
