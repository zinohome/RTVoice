"""Test BaseClient: URL resolution, Bearer headers, response → exception."""
import pytest
import httpx
import respx


def test_per_service_urls_override_base():
    from rtvoice_client._base import _resolve_urls
    urls = _resolve_urls(
        base_url="http://main:80",
        stt_url="http://stt:9090",
        tts_url=None,
        realtime_url="http://rt:9000",
        tokens_url=None,
    )
    assert urls["stt"] == "http://stt:9090"
    assert urls["tts"] == "http://main:80"
    assert urls["realtime"] == "http://rt:9000"
    assert urls["tokens"] == "http://main:80"


def test_base_url_required_when_no_per_service():
    from rtvoice_client._base import _resolve_urls
    with pytest.raises(ValueError):
        _resolve_urls(base_url=None, stt_url=None, tts_url=None,
                      realtime_url=None, tokens_url=None)


def test_bearer_header_added():
    from rtvoice_client._base import _build_headers
    h = _build_headers(api_key="secret")
    assert h["Authorization"] == "Bearer secret"


def test_no_bearer_when_no_api_key():
    from rtvoice_client._base import _build_headers
    h = _build_headers(api_key=None)
    assert "Authorization" not in h


@respx.mock
def test_response_with_error_body_raises_typed():
    from rtvoice_client.errors import PromptTooLong
    from rtvoice_client._base import _check_response
    respx.post("http://x/y").respond(
        422,
        json={"type": "error", "code": "prompt.too_long",
              "message": "too long", "request_id": "r1"},
    )
    resp = httpx.post("http://x/y")
    with pytest.raises(PromptTooLong):
        _check_response(resp)


@respx.mock
def test_response_with_2xx_returns_normally():
    from rtvoice_client._base import _check_response
    respx.get("http://x/y").respond(200, json={"ok": True})
    resp = httpx.get("http://x/y")
    _check_response(resp)
