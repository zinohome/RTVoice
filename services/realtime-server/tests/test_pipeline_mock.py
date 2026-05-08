"""Test pipeline.run_turn() with mocked STT/LLM/TTS clients."""
import asyncio
import json
import pytest
from datetime import datetime, timezone


class FakeSTTClient:
    def __init__(self, final_text="hello world"):
        self.final_text = final_text
        self.feed_calls = []

    async def feed(self, pcm: bytes) -> None:
        self.feed_calls.append(pcm)

    async def request_final(self, timeout: float = 5.0) -> str:
        return self.final_text


class FakeLLMClient:
    def __init__(self, deltas=None):
        self.deltas = deltas or ["你好", "世界"]

    async def stream(self, text: str):
        for d in self.deltas:
            yield d


class FakeTTSWS:
    def __init__(self, pcm_chunks=None):
        self.pcm_chunks = pcm_chunks or [b"\x00" * 480, b"\x00" * 480]
        self.sent_texts = []
        self.eos_called = False
        self.closed = False

    async def send_text(self, text: str) -> None:
        self.sent_texts.append(text)

    async def eos(self) -> None:
        self.eos_called = True

    async def audio_chunks(self):
        for c in self.pcm_chunks:
            yield c

    async def aclose(self) -> None:
        self.closed = True


class FakeTTSClient:
    def __init__(self):
        self.opened_ws = None

    async def open_ws(self):
        self.opened_ws = FakeTTSWS()
        return self.opened_ws


class FakeWS:
    def __init__(self):
        self.sent: list = []
        self.closed = False
        self.close_code = None

    async def send_json(self, obj) -> None:
        self.sent.append(("text", obj))

    async def send_bytes(self, b: bytes) -> None:
        self.sent.append(("bytes", b))

    async def close(self, code=1000, reason=""):
        self.closed = True
        self.close_code = code


def _make_session():
    from app.session_manager import Session
    return Session(
        id="sess_test123",
        creator_key_hash="h",
        voice="default_zh_female",
        speed=1.0,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_run_turn_happy_path():
    """完整 turn: STT final → LLM stream → TTS WS → PCM out → done."""
    from app.pipeline import run_turn
    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="你好")
    sess.llm_client = FakeLLMClient(deltas=["回", "复"])
    sess.tts_client = FakeTTSClient()
    ws = FakeWS()
    await run_turn(sess, ws)
    text_events = [e[1] for e in ws.sent if e[0] == "text"]
    bytes_events = [e[1] for e in ws.sent if e[0] == "bytes"]
    assert {"type": "transcript.final", "text": "你好"} in text_events
    assert any(e.get("type") == "response.done" for e in text_events)
    assert len(bytes_events) >= 1


@pytest.mark.asyncio
async def test_run_turn_empty_stt_emits_error():
    """STT final 为空时发 stt.empty error，不调 LLM/TTS。"""
    from app.pipeline import run_turn
    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="")
    sess.llm_client = FakeLLMClient()
    sess.tts_client = FakeTTSClient()
    ws = FakeWS()
    await run_turn(sess, ws)
    text_events = [e[1] for e in ws.sent if e[0] == "text"]
    assert any(e.get("type") == "error" and e.get("code") == "stt.empty"
               for e in text_events)
    assert sess.tts_client.opened_ws is None


@pytest.mark.asyncio
async def test_run_turn_llm_failure_emits_error():
    """LLM stream 抛异常 → 发 llm.failed error。"""
    from app.pipeline import run_turn
    class BrokenLLM:
        async def stream(self, text):
            if False:
                yield None
            raise RuntimeError("llm crashed")
    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="x")
    sess.llm_client = BrokenLLM()
    sess.tts_client = FakeTTSClient()
    ws = FakeWS()
    await run_turn(sess, ws)
    text_events = [e[1] for e in ws.sent if e[0] == "text"]
    assert any(e.get("type") == "error" for e in text_events)


@pytest.mark.asyncio
async def test_run_turn_clears_current_task_on_finally():
    """run_turn finishes → sess.current_turn_task = None."""
    from app.pipeline import run_turn
    sess = _make_session()
    sess.stt_client = FakeSTTClient(final_text="x")
    sess.llm_client = FakeLLMClient()
    sess.tts_client = FakeTTSClient()
    sess.current_turn_task = "should-be-cleared"
    ws = FakeWS()
    await run_turn(sess, ws)
    assert sess.current_turn_task is None
