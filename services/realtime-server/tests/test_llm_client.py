"""Test LLMClient.stream(messages) signature 改造（SP3 D-2026-05-09-A.5）."""
import inspect


def test_stream_signature_takes_messages_list():
    """stream() 第一参数应该叫 messages，类型是 list；废弃 user_text 旧签名."""
    from app.llm_client import LLMClient
    sig = inspect.signature(LLMClient.stream)
    params = list(sig.parameters.keys())
    assert params[0] == "self"
    assert params[1] == "messages"
