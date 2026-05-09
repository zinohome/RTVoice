"""Test TTS namespace."""
import pytest
import respx
import httpx


@pytest.mark.asyncio
async def test_synthesize_returns_bytes():
    from rtvoice_client.tts import AsyncTTS
    with respx.mock:
        respx.post("http://tts:9880/v1/tts/stream").respond(
            200, content=b"\x01\x02\x03", headers={"Content-Type": "audio/pcm"},
        )
        async with httpx.AsyncClient() as h:
            tts = AsyncTTS(h, "http://tts:9880")
            pcm = await tts.synthesize("hi")
            assert pcm == b"\x01\x02\x03"


@pytest.mark.asyncio
async def test_synthesize_passes_voice_speed_lang():
    from rtvoice_client.tts import AsyncTTS
    with respx.mock:
        route = respx.post("http://tts:9880/v1/tts/stream").respond(200, content=b"x")
        async with httpx.AsyncClient() as h:
            tts = AsyncTTS(h, "http://tts:9880")
            await tts.synthesize("hi", voice="alice", speed=1.5, lang="cmn")
        body = route.calls.last.request.read()
        import json as _json
        parsed = _json.loads(body)
        assert parsed["voice"] == "alice"
        assert parsed["speed"] == 1.5
        assert parsed["lang"] == "cmn"


@pytest.mark.asyncio
async def test_stream_yields_chunks():
    from rtvoice_client.tts import AsyncTTS
    with respx.mock:
        # respx streams full response, httpx.stream() reads it in chunks
        respx.post("http://tts:9880/v1/tts/stream").respond(
            200, content=b"abcdefghi", headers={"Content-Type": "audio/pcm"}
        )
        async with httpx.AsyncClient() as h:
            tts = AsyncTTS(h, "http://tts:9880")
            chunks = [c async for c in tts.stream("hi")]
            # When httpx streams the full content, it yields chunks
            assert b"".join(chunks) == b"abcdefghi"


def test_sync_synthesize_calls_async():
    from rtvoice_client.tts import SyncTTS, AsyncTTS
    from unittest.mock import AsyncMock, MagicMock
    inner = MagicMock(spec=AsyncTTS)
    inner.synthesize = AsyncMock(return_value=b"\x00")
    sync = SyncTTS(inner)
    assert sync.synthesize("hi") == b"\x00"
