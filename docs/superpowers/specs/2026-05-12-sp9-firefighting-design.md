# SP9 — Firefighting + 文档真实化 设计

**Why**：SP8 Dogfooding D1-D4 暴露：纸面 ✅ 的功能在真消费者眼里大量不可用。本 SP 不做新 feature，只**让现有平台真能被消费者用**。

**Non-goal**：G3 per-key metrics / G4 真 OpenAPI 落地（→ SP10）。本 SP 内只**记录**这些缺口，不动它。

## A 部分 — 救火（消费者直接卡住的协议/部署 bug）

### A1 — WS subprotocol echo 修复（**最高优先级**）

- **现状**：`services/realtime-server/app/main.py` `_extract_ws_bearer_key()` 接受 `Sec-WebSocket-Protocol: bearer.<token>` 但 `websocket.accept()` 不带 subprotocol 参数 → 101 响应不 echo → Chrome/Firefox 按 RFC 6455 关连接 (1006)
- **修复**：从 client 提议的 protocol list 选首个匹配项（如 `bearer.<token>`），`accept(subprotocol=proto)` 回去
- **影响面**：3 个服务的 WS endpoints
  - realtime-server: `/v1/sessions/{id}/ws`（D4 实测炸）
  - stt-server: `/v1/asr` `/v1/stt/stream`
  - tts-server: `/v1/tts/stream_ws`
  - 全要检查同样 fix

### A2 — Playwright headless 浏览器回归测试

- 加 `tests/e2e/test_browser_ws.py`（或 similar 路径）
- 用真 Playwright client 连 WS with subprotocol，assert 101 + 协议层正常 close（非 1006）
- 永久守门：CI 跑这个测试避免 A1 类型回归

### A3 — `ws_url` 外部可达性

- **现状**：`SessionCreateResponse.ws_url` 返 `ws://realtime-server:9000/...` 容器主机名
- **修复**：读 `X-Forwarded-Host` / `Host` header，或返 path-only（`/v1/sessions/{id}/ws`）让客户端自己拼

### A4 — STT/TTS prod 端口对外暴露

- **现状**：`docker-compose.prod.yml` 没 expose stt:9090 / tts:9880 到 host
- **决策**：直接在 prod compose expose 到 `${BIND_HOST}:9090` `${BIND_HOST}:9880`
  - 不引入 caddy/nginx 反代（控制 SP9 范围）
  - TLS 留 SP10 或后续
- **影响**：platform vision "三对等 offering" 在 prod URL 表面真生效

### A5 — web demo 真打包进镜像

- **现状**：prod `services/realtime-server/static/index.html` 是 SP3 旧 4KB 页；`clients/web/` 4-tab demo 没打包进
- **修复**：`services/realtime-server/Dockerfile` `COPY services/realtime-server/static` → 改 `COPY clients/web /app/static`
- **决策**：clients/web/ 作 prod demo 真实部署源；删 `services/realtime-server/static/` 或保留作 fallback
- 检查 `clients/web/` 里 `API base URL` 默认值，确保用 `location.origin` 让浏览器自动拼

### A6 — session lifecycle 完整化

- **现状**：HTTP `POST /v1/sessions` 创建后无对应 DELETE；session 卡满靠 30s idle 回收（D3-S2）
- **修复**：
  - 加 `DELETE /v1/sessions/{id}`（auth + scope）
  - `SessionManager.idle_timeout` env 化（默认从 30s → 5s 更激进，避免 dogfood 卡死）
- 加测试

## C 部分 — 文档真实化（消费者被骗的地方）

### C1 — `COZYVOICE_INTEGRATION.md` 大改

- **新增 §0 (顶部)**：端口拓扑明确章节
  - Topology A（同机 docker network） vs B（外网 host:port）
  - 哪些端口必须对外、哪些容器内即可
  - 与 A4/A5 同步
- **重写 §2.1**：颁 key 流程改 v0.14
  - 用 `rtvoice-admin create --name ... --scopes ...`
  - 不再 single-key + rebuild
  - admin API 提一下（如已实现）
- **新增 §"凭据分层"**：Bearer key vs LiveKit JWT vs session_id 三种凭据用途/获取方式/作用域
- **新增 §"scopes 清单"**：stt / tts / tokens / realtime 各自含义 + 拒绝示例
- **修 §3.3**：D1-F8 `urllib.request.urlopen(headers=)` 语法错例
- **修 §"voice clone"**：D1-F7 `/v1/voices` read vs write 鉴权要求混表
- **修 §音频格式**：D1-F5 加 sample_rate / channels / bytes_per_sample 明确表格
- **修 §"websockets 库版本"**：D1-F6 至少声明最低版本
- **加 §"自环测试 caveats"**：D1-F9 自环识别会噪，不代表真录音质量

### C2 — `rtvoice-client` 包真实化

- **D1-F10**：文档说 `pip install rtvoice-client` 但 PyPI 没有
- **决策选项**（开始前要定）：
  - (a) 真发布到 PyPI（费时）
  - (b) 改文档为 "internal-only / coming soon"
  - (c) 文档 link 到 GitHub raw 仓库的 `clients/python/` 让 pip 直装 git URL
- **建议 (b) + (c)**：现阶段不上 PyPI，文档给 git URL pip install 命令

## Done 定义

A 部分硬标准：
- A1：3 个 WS endpoint 全修，Playwright 测试通过（用 chromium headless 真连 WS 看 101 + subprotocol echo）
- A2：回归测试在 CI 跑（这个 SP 也包括 CI 集成）
- A3：`ws_url` 在 prod 真外网调用时浏览器能直接拼对
- A4：`curl http://192.168.66.163:9880/health` (or whatever) 通；`/v1/tts/synthesize` 外网 200
- A5：浏览器打开 `http://192.168.66.163:9000/static/` 看到 4-tab demo（不是 SP3 旧页）
- A6：`DELETE /v1/sessions/{id}` 端点存在 + 测试 + idle_timeout 5s 默认

C 部分硬标准：
- C1：D1 finding F1-F10 全部对应一条或多条文档改动；INTEGRATION.md 顶部有端口拓扑章节
- C2：文档不再说 `pip install rtvoice-client`（要么真发布要么改文案）

## 估时

A 部分 ≈ 1 天 / C 部分 ≈ 0.5 天 / 总 1.5 天 / 6-8 个 T

## Prod 验收（按 A/B prod-gate）

类 B (脚本化)：
- 4 个端口外网 curl 通
- Playwright WS 测试在 CI 通
- session DELETE 端点行为正确

类 A (进 MANUAL_VALIDATION_QUEUE)：
- 浏览器真打开 demo 4 tab 跑一遍
- 真录音 ASR 准确度（D4 未 cover 项）
- 长稳跑

## 风险

- A4 暴露端口 = 增加攻击面。修前确认 Bearer 鉴权对**所有** STT/TTS endpoint 都强制（D1 自验时 stt 9090 直接 GET /info 不需要 key——这可能是新 finding）
- A5 改 Dockerfile.realtime-server 要重 build → 镜像 layer cache 命中应该不影响 ML 层
- A1 改 WS protocol 要回归测试守门，否则下次又会 break
