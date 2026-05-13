"""Test STT namespace: transcribe + stream (mocked httpx)."""
import pytest
import respx
import httpx


# SP13 T6: transcribe() 之前错以为 RTVoice STT 有 HTTP `/v1/asr` REST 端点；
# 实际只 WS。现在 transcribe() 内部走 WS。HTTP-mock 测试已不适用——WS 协议层
# mock 太复杂（必须模拟 server 端 final 事件），改在 scripts/e2e-smoke.sh 真测。
@pytest.mark.skip(reason="SP13 T6: transcribe 现走 WS-only；e2e 测试覆盖（scripts/e2e-smoke.sh）")
@pytest.mark.asyncio
async def test_async_transcribe_returns_text():
    pass


@pytest.mark.skip(reason="SP13 T6: transcribe 现走 WS-only；e2e 测试覆盖")
@pytest.mark.asyncio
async def test_async_transcribe_raises_on_4xx():
    pass


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
