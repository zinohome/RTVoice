"""Test Tokens namespace."""
import pytest
import respx
import httpx


@pytest.mark.asyncio
async def test_livekit_returns_typed_response():
    from rtvoice_client.tokens import AsyncTokens
    from rtvoice_client.models import TokenResponse
    with respx.mock:
        respx.post("http://tok:8000/v1/tokens").respond(
            200, json={"token": "eyJ...", "url": "ws://lk:7880",
                       "room": "r", "identity": "alice"},
        )
        async with httpx.AsyncClient() as h:
            tok = AsyncTokens(h, "http://tok:8000")
            r = await tok.livekit(identity="alice", room="r", ttl_minutes=10)
        assert isinstance(r, TokenResponse)
        assert r.token == "eyJ..."


@pytest.mark.asyncio
async def test_livekit_auth_error_raises():
    from rtvoice_client.tokens import AsyncTokens
    from rtvoice_client.errors import AuthError
    with respx.mock:
        respx.post("http://tok:8000/v1/tokens").respond(
            401,
            json={"type": "error", "code": "auth.invalid_token",
                  "message": "x", "request_id": "r"},
        )
        async with httpx.AsyncClient() as h:
            tok = AsyncTokens(h, "http://tok:8000")
            with pytest.raises(AuthError):
                await tok.livekit(identity="a", room="b")
