"""Test that Client (sync) and AsyncClient (async) expose 4 namespaces correctly."""


def test_async_client_has_4_namespaces():
    from rtvoice_client import AsyncClient
    c = AsyncClient(api_key="k", base_url="http://x")
    from rtvoice_client.stt import AsyncSTT
    from rtvoice_client.tts import AsyncTTS
    from rtvoice_client.realtime import AsyncRealtime
    from rtvoice_client.tokens import AsyncTokens
    assert isinstance(c.stt, AsyncSTT)
    assert isinstance(c.tts, AsyncTTS)
    assert isinstance(c.realtime, AsyncRealtime)
    assert isinstance(c.tokens, AsyncTokens)


def test_sync_client_has_4_namespaces():
    from rtvoice_client import Client
    c = Client(api_key="k", base_url="http://x")
    from rtvoice_client.stt import SyncSTT
    from rtvoice_client.tts import SyncTTS
    from rtvoice_client.realtime import SyncRealtime
    from rtvoice_client.tokens import SyncTokens
    assert isinstance(c.stt, SyncSTT)
    assert isinstance(c.tts, SyncTTS)
    assert isinstance(c.realtime, SyncRealtime)
    assert isinstance(c.tokens, SyncTokens)
