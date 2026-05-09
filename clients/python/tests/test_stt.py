"""Test STT namespace: transcribe + stream (mocked httpx)."""
import pytest
import respx
import httpx


@pytest.mark.asyncio
async def test_async_transcribe_returns_text():
    from rtvoice_client.stt import AsyncSTT
    with respx.mock:
        respx.post("http://stt:9090/v1/asr").respond(
            200, json={"text": "你好世界"}
        )
        async with httpx.AsyncClient() as h:
            stt = AsyncSTT(h, "http://stt:9090")
            text = await stt.transcribe(b"\x00" * 100, sample_rate=16000)
            assert text == "你好世界"


@pytest.mark.asyncio
async def test_async_transcribe_raises_on_4xx():
    from rtvoice_client.stt import AsyncSTT
    from rtvoice_client.errors import ValidationError
    with respx.mock:
        respx.post("http://stt:9090/v1/asr").respond(
            422, json={"type": "error", "code": "validation.invalid_request",
                       "message": "bad pcm", "request_id": "r1"},
        )
        async with httpx.AsyncClient() as h:
            stt = AsyncSTT(h, "http://stt:9090")
            with pytest.raises(ValidationError):
                await stt.transcribe(b"x", sample_rate=16000)


def test_sync_transcribe_calls_async_via_run():
    from rtvoice_client.stt import SyncSTT, AsyncSTT
    from unittest.mock import AsyncMock, MagicMock
    inner = MagicMock(spec=AsyncSTT)
    inner.transcribe = AsyncMock(return_value="hello")
    sync = SyncSTT(inner)
    result = sync.transcribe(b"x", sample_rate=16000)
    assert result == "hello"


@pytest.mark.asyncio
async def test_async_stream_context_manager():
    from rtvoice_client.stt import AsyncSTT
    async with httpx.AsyncClient() as h:
        stt = AsyncSTT(h, "http://stt:9090")
        cm = stt.stream(ws_url="ws://stt:9090/v1/asr")
        assert hasattr(cm, "__aenter__")
        assert hasattr(cm, "__aexit__")
