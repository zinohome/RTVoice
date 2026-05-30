# SP9 Firefighting + 文档真实化 实施 plan

## A 部分 — 救火

### T1 — WS subprotocol echo 修复（3 service）

- `services/realtime-server/app/main.py` `_extract_ws_bearer_key` 区域：从 `ws.headers["sec-websocket-protocol"]` 解析 list，选首个 `bearer.*` 作 `accepted_proto`，传给 `websocket.accept(subprotocol=accepted_proto)`
- 同改 stt-server 和 tts-server 的 WS endpoints（grep `websocket.accept(` 找全部）
- 加单测：模拟 client 发 `Sec-WebSocket-Protocol: bearer.xxx`，assert response 含同 header

### T2 — Playwright headless 浏览器 WS 回归测试

- 新增 `tests/e2e/test_browser_ws.py` 用 playwright sync API
- 启 realtime-server (sandbox compose) → 创 session → 用 chromium WebSocket API 连 ws_url + subprotocol → assert open event 触发 + close code != 1006
- CI 集成：`.github/workflows/*` 加 `pytest tests/e2e/test_browser_ws.py`（需要 `playwright install chromium`）

### T3 — `ws_url` 外部可达

- `services/realtime-server/app/main.py` `SessionCreateResponse.ws_url` 构造逻辑：
  - 读 `X-Forwarded-Host` / `Host` 优先于 docker hostname
  - 或改为 path-only `/v1/sessions/{id}/ws` + 文档说"客户端自己拼 `wss://<host>/v1/...`"
- 测试覆盖 X-Forwarded-Host / 无 header / 容器内 fallback 三场景

### T4 — prod stt/tts 端口对外

- `docker-compose.prod.yml` stt-server 加 `ports: ["${BIND_HOST}:${STT_PORT:-9090}:9090"]`
- 同 tts-server 加 `${TTS_PORT:-9880}:9880`
- `.env.example` 加 `STT_PORT=9090 TTS_PORT=9880`
- **关键**：先确认 stt/tts 的 `require_key` 对**所有**业务端点都覆盖（不只 /v1/asr，还有 /info / /v1/voices 等）；漏的补上
- prod 应用：rebuild + recreate stt/tts 容器

### T5 — web demo 真打包

- `services/realtime-server/Dockerfile`：把 `COPY services/realtime-server/static /app/static` 改成 `COPY clients/web /app/static`
- 检查 `clients/web/index.html` 里 `<script>` 默认 API base URL，确保用 `location.origin` 或可配
- 删除 `services/realtime-server/static/`（如确认已被 clients/web/ 完全替代）或保留作 fallback
- prod 应用：rebuild realtime-server

### T6 — session lifecycle

- `services/realtime-server/app/main.py` 加 `DELETE /v1/sessions/{id}`
  - 鉴权 scope=realtime；session 不存在返 404
  - 调用 SessionManager.close(session_id)
- `SessionManager.idle_timeout` env 化：`RTVOICE_SESSION_IDLE_S=5`（默认改 5s）
- 测试覆盖 DELETE / idle 自动回收

## C 部分 — 文档

### T7 — `COZYVOICE_INTEGRATION.md` 大改

按 spec 顺序：
- 加 §0 端口拓扑
- 重写 §2.1 颁 key 流程
- 加 §凭据分层
- 加 §scopes 清单
- 修 §3.3 / §voice-clone / §音频格式 / §websockets 版本
- 加 §自环测试 caveats
- 处理 `rtvoice-client` install 表述（C2 决策定后落地）

### T8 — CHANGELOG v0.15.0

按 v0.14.0 段落格式写：Added / Changed / Fixed / Notes / 验证（autonomous + 待用户）

---

## 完工标准

- 6 个 A 类 T + 2 个 C 类 T 全完工
- prod 实测：
  - 浏览器从 host 端口连 WS 不再 1006
  - prod stt/tts 端口 curl 通
  - 浏览器打开 prod web demo 看到 4-tab
- INTEGRATION.md 10 个 D1 finding 全部对应改动

## 风险监控

- A4 prod 端口暴露前必 audit 鉴权覆盖；任何 endpoint 漏 require_key 视为新 P0 finding 立刻补
- A5 删 services/realtime-server/static/ 前确认无别处依赖
- A1 WS subprotocol echo 必先 Playwright 测试通过再 push
