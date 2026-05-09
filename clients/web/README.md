# RTVoice Web Demo

纯 HTML/CSS/ES modules，零 build 链。

## 运行

### 本地（推荐）
```bash
cd clients/web/
python3 -m http.server 8080
# 浏览器开 http://localhost:8080/
```

### 通过 nginx 静态部署
将整个 `clients/web/` 目录拷到 nginx web root。

### 通过 RTVoice realtime-server co-host
```bash
docker cp clients/web rtvoice-realtime:/app/static/web
# 浏览器 http://${host}:9000/static/web/
```

## 配置

页面顶部 config bar：

- **API base**: `http://your-rtvoice-host:9000`（默认 `http://127.0.0.1:9000`）
- **Bearer**: dev 模式留空；prod 填 `RTVOICE_API_KEY`

存 `localStorage.rtvoice_base` / `localStorage.rtvoice_bearer`。

## 4 Tabs

| Tab | 演示 |
|---|---|
| STT | Mic 录音 → /v1/asr 一次性识别 |
| TTS | 中文 → /v1/tts/stream → Web Audio 播放 |
| Realtime | 完整对话流（含 transcript.partial / response.text 流式渲染 + session.update + memory.clear） |
| Tokens | LiveKit token 申请 |

## 浏览器要求

- Chrome / Edge / Firefox / Safari ≥2024
- HTTPS 或 localhost（mic 权限必需）
- WebSocket / Web Audio API 支持

## CORS

后端默认 `*`（dev 友好）；prod 收紧用 env `RTVOICE_CORS_ORIGINS`。
