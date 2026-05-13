# Python Quickstart Sample

最小可运行的 RTVoice 接入示例。验证 token / TTS / Realtime 三路 HTTP 接通。

## 跑法

```bash
# 1. 拉 Caddy root CA（信任 TLS 自签）
../../scripts/get-rtvoice-ca.sh

# 2. 装依赖
pip install -e ../../clients/python httpx python-dotenv

# 3. 配 env
cp .env.example .env
# 编辑 .env 填 RTVOICE_API_KEY（admin CLI create 出来的 secret）

# 4. 跑
python main.py
```

期望输出：
```
▶ Target: https://192.168.66.163   verify=../../caddy-root.crt
✅ /info — realtime-server v0.17.1
✅ /v1/tokens → JWT (len=348, room=quickstart-demo)
✅ /v1/tts/stream → 96000 bytes (2.00s @ 24kHz, server time 1.2s)
   → saved hello.wav (24kHz mono)
✅ /v1/sessions → sess_xxx
   ws_url = wss://192.168.66.163/v1/realtime/sess_xxx
✅ DELETE /v1/sessions/sess_xxx → 204

🎉 全 4 service 接通 OK。
```

## 没涵盖的

- **STT** — 是 WS 流式协议，不在 HTTP 简单示例范围内。完整 STT 用法见 `examples/browser-quickstart/`（浏览器端）或 SDK `from rtvoice_client.stt import AsyncSTT`
- **TTS WebSocket streaming** — 双向流式（text 流入 → audio 流出），低延迟场景。本示例用单次 POST 简化
- **Voice clone** — admin endpoint POST/DELETE /v1/voices，需要 `TTS_ADMIN_API_KEY`

## 排查

| 现象 | 排查 |
|---|---|
| 第 1 行 `verify=OFF` 但你在生产 | `.env` 里 `RTVOICE_CA_FILE` 没设；运行 `../../scripts/get-rtvoice-ca.sh` |
| `❌ /info 失败 HTTP 000` | Caddy 没起 / 网络不通；先 `curl -k https://192.168.66.163/info` 看 |
| `❌ token-server 403 — key 缺 scope=tokens` | admin CLI 当时没加 `tokens` scope；rotate 或 create 新 key |
| TTS 返 503 `tts.not_ready` | CosyVoice 模型还在加载（首次启动 5GB 下载）；等 5min |
