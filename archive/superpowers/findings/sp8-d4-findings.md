# SP8 D4 — Web Demo Browser Dogfood Findings

Executor: Playwright MCP, prod host `192.168.66.163:9000`, 2026-05-12.
Bearer key_id `key_CzClq1YYH9ze11_e` (scopes stt,tts,tokens,realtime).
Screenshot: `.playwright-mcp/screen-realtime.png` (only one — see §"4-tab demo doesn't exist").

## 总结

| Tab | 协议层 | UI | 错误处理 |
|---|---|---|---|
| Tokens | ❌ N/A | ❌ N/A | ❌ N/A |
| TTS | ❌ N/A | ❌ N/A | ❌ N/A |
| STT | ❌ N/A | ❌ N/A | ❌ N/A |
| Realtime | ⚠️ REST OK, WS broken in browser | ⚠️ 1 屏 5 按钮 SP3 测试页 | ✅ JSON 错误信封清晰 |

**关键阻塞数：2**（F1 4-tab demo 未部署；F2 WS 子协议握手浏览器不兼容）。
**主观 UX 客观部分**：作为首访开发者，看到 SP3 内部测试页而不是营销/演示页，理解不到 "platform = TTS+STT+RealTime 三对等 offering" 这个 vision；只会以为是一个 realtime voice server 的诊断工具。

## 每 tab 一节

### Tokens / TTS / STT — 全部不存在
- **进入路径**：尝试 `http://192.168.66.163:9000/static/{stt,tts,tokens,realtime}.html` — 全部 404。
- **OpenAPI 暴露的 paths**：`["/metrics", "/health", "/info", "/v1/sessions"]` — 仅 Realtime。
- **端口扫描**：8000/8001/8002/8080/9001/9002 全无服务（CORS 失败/ECONNREFUSED/EMPTY_RESPONSE）。
- **finding**：
  - **F1 [阻塞]** prod 部署的 web demo (`/static/index.html`, 4058 bytes) 是 **SP3 时代的 1-屏 Realtime 测试页**，标题字面写着 "RTVoice Realtime — SP3 Test Page"。spec 提到的 4-tab 演示（STT/TTS/Realtime/Tokens）**根本没有部署**。仓库内 `clients/web/index.html` 看起来是 4-tab 富版本，但**没进生产镜像**。
  - **F2 [阻塞]** prod realtime-server 也**没暴露 `/v1/tts` `/v1/stt` `/v1/tokens`** REST 端点 —— 即便 demo 有了 4-tab UI，也没有后端可调。STT/TTS/Tokens 必须是独立服务，但 host:9000 上只跑 realtime-server v0.12.0。"platform vision 三对等 offering" 在生产 URL 上**完全没体现**。

### Realtime
- **进入路径**：`http://192.168.66.163:9000/static/index.html`。
- **首屏体验**：仅 5 按钮（创建 session / 连 WS / 开始录音 / 结束 turn / 改 prompt）+ 3 输入框（API base, Bearer, Prompt）+ audit_persist checkbox。无说明文档，无"输入 API key 引导"，无"Tokens / STT / TTS 入口"。文案是中文 + "(空=dev)" 提示，对生产环境**误导**——生产不接受空 bearer。
- **localStorage**：未被使用，Bearer 每次刷新都要重填。
- **首屏 console**：1 个无害 favicon 404。
- **关键交互**：
  1. 填 API base = `http://192.168.66.163:9000`，Bearer = 真实 token，Prompt = 任意。
  2. 点 "1) 创建 session" → POST `/v1/sessions` → 201 → 显示 `created sess_xxx`。✅
  3. 点 "2) 连 WS" → 立即 `ws err` + `ws close 1006`。❌
- **观察到的请求 / 响应**：
  - `POST /v1/sessions` → 201。返回 `ws_url: "ws://realtime-server:9000/..."`（**容器主机名**，浏览器不可达）。
  - 客户端 `realtime.js` 有 hostname/port 重写逻辑（rewrite to cfg.base origin），但**部署的 SP3 页面没有这个 fix**，直接用 `127.0.0.1:9000`（页面默认 API base）→ ERR_CONNECTION_REFUSED。
  - 即便手动用 JS 改写到 `ws://192.168.66.163:9000/...` 并带 Bearer 子协议，也 1006。详见 F4。
- **错误路径测试**：
  - 错 Bearer：`401 auth.invalid_token` → UI 显示 "create failed: 401 {...}" ✅。
  - 空 Bearer：`401 auth.missing_token` ✅。
  - 50KB prompt：`422 prompt.too_long` ✅，但 UI 同样只显示 raw JSON。
  - 并发超限：5 个未回收 session 后，`503 session.capacity_full` ✅，但用户无法看到"我能不能再创"提示，盲点。
- **网络面板 dump**（关键，剔除端口探测噪音）：
  - `GET /openapi.json` → 200
  - `POST /v1/sessions` → 201（创建成功）
  - `POST /v1/sessions` → 503（容量满）
  - `GET /health` → 200, `GET /info` → 200
  - `WS /v1/realtime/sess_xxx` → 1006（详见 F4）

- **finding**：
  - **F3 [高]** Server `POST /v1/sessions` 返回的 `ws_url` 用容器主机名 `realtime-server:9000`，外部不可达。客户端必须 hostname 重写。生产部署应该让 server 知道自己的外部 host（环境变量 `RTVOICE_PUBLIC_HOST` 之类），或者干脆只返回 path 让 client 拼接 origin。
  - **F4 [阻塞]** **WS handshake 没有 echo `Sec-WebSocket-Protocol` 响应头**。客户端用 `bearer.<token>` 子协议传 token，服务器 101 升级时不回 protocol → Chrome/Firefox 按 RFC 6455 关连接（1006）。curl 没这个检查，所以容易漏测。**任何浏览器都无法连 WS**——这就是为什么用户报 "WS 一直 1006"。修复点：FastAPI/uvicorn `WebSocket.accept(subprotocol="bearer.<token>")` 必须 echo 收到的子协议。
  - **F5 [中]** SP3 测试页默认 API base = `http://127.0.0.1:9000`，对从外部访问的用户极易掉坑（实际访问的是 192.168.66.163，但 API base 不会自动跟随 location.origin）。建议默认 `window.location.origin`。
  - **F6 [中]** 5 个 session 仍存活时 503 capacity_full，且 `session_idle_timeout_s=30`、`session_max_lifetime_s=1800`。30s idle 在演示场景太短（用户停顿一下就被踢），但 1800s lifetime 又太长（一次失败的 demo 会占坑半小时直到 idle 触发）。结合 F4——浏览器永远连不上 WS，session 创建后就只能等 30s idle 释放。
  - **F7 [中]** 错误展示是 raw JSON 透传到日志区。没有把 `auth.invalid_token`、`session.capacity_full`、`prompt.too_long` 翻译成可操作提示（"请检查 API Key" / "等 30 秒再试" / "Prompt ≤ 2000 字")。
  - **F8 [低]** localStorage/cookie 0 利用：每次刷新清空 Bearer 输入，开发者-体验**反复粘贴 token**。
  - **F9 [低]** 页面无 mic permission UI 引导：点 "3) 开始录音"前没有"将请求 mic 权限"的解释；Playwright 无 mic 不能验真录音路径。
  - **F10 [低]** 无"上传音频文件"备用入口——如果用户没 mic 设备（VM、远程桌面），STT/Realtime 完全跑不起来，没有 fallback。

## 哪些只能用户验（→ MANUAL_VALIDATION_QUEUE）

- **真录音→ASR 准确度**（Playwright 无音频源，且 native lib 并发问题也只能手测）
- **TTS 合成音质 + 音色一致性**（要听）
- **Barge-in 手感**（在 agent 说话时打断的体感延迟、是否吞字）
- **端到端首字延迟主观感受**（需要真 mic + 真耳朵；目标 P50 ≤ 1.5s 是协议指标，不是感受指标）
- **长稳**（10+ 分钟会话内存涨、WS 抖动、重连体验）
- **移动端**（iOS Safari 音频自动播放策略、Android Chrome MediaRecorder codec）
- **Tokens UI** 完全无（页面不存在），无法 dogfood

## 建议（最值得优先做的 3 条修复）

1. **修 F4 — WS 子协议 echo**：`realtime_server/app/ws.py`（或等价路径）`websocket.accept()` 必须把客户端发的 `bearer.<token>` 子协议原样作为 `subprotocol=` 传回。这是**所有浏览器无法连 WS** 的根因，一行修复，比任何 UI 美化都重要。新增回归测试：浏览器 WebSocket headless 跑通一次 open/close（playwright/puppeteer），别只用 `websockets` python lib 测，因为 python lib 不强制子协议 echo。
2. **决定 D4 demo 的真实部署**：要么 (a) 把 `clients/web/` 4-tab 真实构建进 nginx + 部署到 192.168.66.163:9000/static/，并把 STT/TTS/Tokens 服务真的跑起来（暴露 9001/9002/9003 + 配 reverse proxy），要么 (b) 在 SP3 测试页醒目位置标 "internal QA only — public demo TBD"。当前状态对外部访客极易误判 platform 成熟度。
3. **修 F3 + F5 — host fallthrough**：`POST /v1/sessions` 返回的 `ws_url` 改成 schema-relative path 或读 `Forwarded` / `X-Forwarded-Host`；同时 SP3 页默认 API base 改 `window.location.origin`。这两个改完，**curl 测和浏览器测就行为一致**了，下次不会再出现"curl 通浏览器不通"的撕裂感。

## 边界守约

- 未提交任何代码
- 未将 secret 写入本文档或截图
- 翻 `clients/web/` 源码次数：2（`config.js` base URL + `realtime.js` wsUrl rewrite）—— 用于 F2/F3 根因定位，未超 3 次预算
- 截图存于 `.playwright-mcp/screen-realtime.png`（`/tmp/sp8-d4/` 不在 playwright allowed roots，已说明）
