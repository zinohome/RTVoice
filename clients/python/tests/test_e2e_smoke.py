"""End-to-end smoke against real RTVoice prod.

跑：cd clients/python && pytest -m e2e -v
仅在 RTVOICE_E2E_BASE 环境变量设置时跑（默认跳过）。
"""
import os
import pytest


pytestmark = pytest.mark.e2e


@pytest.fixture
def base_url():
    url = os.environ.get("RTVOICE_E2E_BASE")
    if not url:
        pytest.skip("RTVOICE_E2E_BASE not set; skipping e2e")
    return url


@pytest.fixture
def api_key():
    return os.environ.get("RTVOICE_E2E_API_KEY", "")


def test_e2e_info_endpoint_reachable(base_url, api_key):
    """GET /info 返回 SP3 capabilities."""
    import httpx
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    r = httpx.get(f"{base_url}/info", headers=headers, timeout=10)
    r.raise_for_status()
    caps = r.json()["capabilities"]
    assert caps["memory"] is True
    assert "default_prompt" in caps


def test_e2e_create_and_get_session(base_url, api_key):
    """POST /v1/sessions → SDK 解析；prompt 透传 OK。"""
    from rtvoice_client import Client
    c = Client(api_key=api_key or None, base_url=base_url)
    sess = c.realtime.create_session(prompt="e2e test prompt", audit_persist=False)
    assert sess.session_id.startswith("sess_")
    assert sess.prompt == "e2e test prompt"


def test_e2e_prompt_too_long(base_url, api_key):
    """超长 prompt → SDK 抛 PromptTooLong。"""
    from rtvoice_client import Client
    from rtvoice_client.errors import PromptTooLong
    c = Client(api_key=api_key or None, base_url=base_url)
    with pytest.raises(PromptTooLong):
        c.realtime.create_session(prompt="x" * 9999)
