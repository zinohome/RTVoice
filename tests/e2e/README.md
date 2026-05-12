# E2E Browser Tests

真浏览器（chromium headless）跑的回归测试。守门 SP8 D4-F4 那种"curl 测过但浏览器连不上"
的 RFC-strict 协议层 bug。

## 本地跑

```bash
python3 -m venv .e2e-venv
source .e2e-venv/bin/activate
pip install playwright pytest-asyncio pytest fastapi uvicorn 'pydantic>=2'
playwright install chromium
PYTHONPATH=services/realtime-server:services/common pytest tests/e2e/ -v
```

## CI

见 `.github/workflows/browser-e2e.yml`。

## 为什么必要

starlette TestClient 和 curl 不强制 RFC 6455 §4.2.2 (Sec-WebSocket-Protocol echo)。
所以"测试都过 + curl 通"≠"浏览器能用"。真浏览器是唯一能守住协议层正确性的回归测试。
