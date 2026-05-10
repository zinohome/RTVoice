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

## 故障排查

### 浏览器 console 报 CORS 错

- 检查后端服务是否启 CORS：`curl -i -X OPTIONS http://your-host:9000/v1/sessions -H "Origin: http://localhost:8080"` 返 `access-control-allow-origin` 头即 ok
- 后端默认 `RTVOICE_CORS_ORIGINS=*`；prod 收紧后浏览器 origin 必须在列表内

### Tokens tab 调用 token-server 被 CORS 拦

token-server 默认**不**加 CORS（避免 LiveKit secret 暴露面）。
浏览器测试 token API 时，临时给 token-server 加 CORS（仅 dev）：

修改 `services/token-server/app/main.py` 同款加 `CORSMiddleware`；或走前端代理。

### Mic 权限拒绝

- 必须 `localhost`（http 也行）或 HTTPS 域名才允许 mic
- 浏览器地址栏点小锁 → 「网站设置」→ 麦克风「允许」

### 远程访问 RTVoice prod

- API base 填公网/内网地址：`http://192.168.66.163:9000`
- WS URL 自动用 cfg.base 的 hostname 重写
