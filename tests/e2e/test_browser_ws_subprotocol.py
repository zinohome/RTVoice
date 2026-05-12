"""SP9 T2 — 真浏览器 (chromium headless) 守门 WS Sec-WebSocket-Protocol echo。

背景：SP8 D4-F4 发现 server `ws.accept()` 不带 subprotocol 时，starlette
TestClient + curl 都不报错，但 Chrome / Firefox 按 RFC 6455 §4.2.2 直接
close(1006) → 所有浏览器无法连 WS。

这个测试用真 chromium 跑，是唯一能在 CI 阶段守住此类协议层回归的方式。

本地跑：见 tests/e2e/README.md
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
REALTIME_DIR = REPO_ROOT / "services" / "realtime-server"
COMMON_DIR = REPO_ROOT / "services" / "common"
TOKEN = "e2e-test-32chars-aaaaaaaaaaaaaaaa"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server_url(tmp_path_factory):
    port = _free_port()
    keys_dir = tmp_path_factory.mktemp("keys")
    env = {
        **os.environ,
        "RTVOICE_KEYS_BACKEND": "yaml",
        "RTVOICE_KEYS_FILE": str(keys_dir / "keys.yaml"),
        "RTVOICE_API_KEY": TOKEN,
        "RTVOICE_MAX_CONCURRENT_SESSIONS": "5",
        "RTVOICE_CORS_ORIGINS": "*",
        "PYTHONPATH": f"{REALTIME_DIR}:{COMMON_DIR}",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        cwd=str(REALTIME_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{base}/health", timeout=0.5)
            break
        except Exception:
            time.sleep(0.3)
    else:
        proc.terminate()
        out = (proc.stdout.read() or b"").decode()[-1000:]
        raise RuntimeError(f"realtime-server failed to start. tail:\n{out}")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _create_session(base: str) -> str:
    req = urllib.request.Request(
        f"{base}/v1/sessions",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    body = urllib.request.urlopen(req).read().decode()
    return json.loads(body)["session_id"]


@pytest.mark.asyncio
async def test_browser_ws_connects_with_bearer_subprotocol(server_url):
    """真浏览器用 ["bearer.<token>"] 子协议连 WS，必须 onopen 触发不被 close(1006)。"""
    sid = _create_session(server_url)
    ws_host = server_url.replace("http://", "")
    ws_url = f"ws://{ws_host}/v1/realtime/{sid}"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            result = await page.evaluate(
                """async ({ wsUrl, token }) => {
                    return await new Promise((resolve) => {
                        const ws = new WebSocket(wsUrl, [`bearer.${token}`]);
                        ws.onopen = () => resolve({
                            ok: true,
                            protocol: ws.protocol,
                            readyState: ws.readyState,
                        });
                        ws.onclose = (e) => resolve({
                            ok: false,
                            code: e.code,
                            reason: e.reason,
                            wasClean: e.wasClean,
                        });
                        setTimeout(() => resolve({ ok: false, code: -1, reason: "timeout" }), 5000);
                    });
                }""",
                {"wsUrl": ws_url, "token": TOKEN},
            )
        finally:
            await browser.close()

    assert result.get("ok") is True, (
        f"Browser failed to connect — pre-SP9-T1 bug regressed: {result}"
    )
    assert result["protocol"] == f"bearer.{TOKEN}", (
        f"Server didn't echo subprotocol back: {result}"
    )
    assert result["readyState"] == 1, f"WS not OPEN: {result}"
