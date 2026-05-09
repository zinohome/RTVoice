# Changelog

RTVoice 项目从立项到 dev 全链路上线的版本记录。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号语义同 [SemVer](https://semver.org/lang/zh-CN/)。

每个版本含：
- **Added** 新增能力 / **Changed** 变更 / **Fixed** 修复 / **Notes** 决策与教训
- **验证**：autonomous（自动化）+ 待人工 部分各列

---

## [Unreleased]

待规划：
- agent-worker 把 LLM token 流直接喂 v0.7 inference_zero_shot generator（享受 150ms 真端到端）
- v0.7 prod GPU 实测 + tweak（浏览器端 UX 验证、长稳测试、subjective 音质对比）
- 多 agent 实例 / function calling / 长上下文记忆
- v0.6 Dockerfile chown 优化跟随 prod build 验证（仅理论 trust，未实测）

---

## [0.11.0] — 2026-05-09 — SP4 Bridge: Python SDK + SP3 残项 + A-lite 仪表盘

平台化重构第五阶段：把 RTVoice 从"platform 已建好"推进到"platform 已能被用起来 + 看得见"。

### Added

- **`clients/python/`** — Python SDK `rtvoice-client` v0.1.0 alpha（PyPI 公开发布）
  - 4 命名空间：stt / tts / realtime / tokens
  - async + sync 双形态（`AsyncClient` + `Client`）
  - 14 typed exception 对应 CONVENTIONS §6 错误码
  - Pydantic v2 模型 + RealtimeEvent discriminated union
  - 高层 `realtime.conversation()` helper
  - `py.typed` marker
- **`monitoring/`** — Prometheus + Grafana provisioning（profile=monitoring）
  - `prometheus.yml` (4 services scrape, 15d retention)
  - `grafana/dashboards/rtvoice-overview.json` (8 面板)
  - `docker-compose.yml` 加 prometheus + grafana service block
- **realtime-server** 3 个新 Prometheus metric
  - `rtvoice_realtime_sessions_active` Gauge
  - `rtvoice_realtime_turns_total` Counter (status=ok|error)
  - `rtvoice_realtime_audit_queue_depth` Gauge

### Changed

- `WS session.update` 白名单：`prompt` → `prompt + voice + speed`
- `pipeline.run_turn`：检查 `sess.tts_client_dirty`，需要时关旧 TTSClient + 用新 voice/speed 建新
- `WS` 新增 `memory.clear` event（清 session 历史，prompt 不动）
- `Session` dataclass 加 `tts_client_dirty: bool` 字段
- `ConversationMemory` 加 `.clear()` 方法

### 验证（autonomous）

- ✅ SDK 共 56 单元测试（smoke + errors + models + base + stt + tts + realtime + tokens + sync + 3 e2e skipped）
- ✅ realtime-server 60 测试（K 残项 +5 + metrics +1）
- ✅ SP3 后 52 → SP4 后 116（SDK + realtime + e2e 占位）
- ✅ docker compose validate OK
- ⏳ prod 集成测试 + user-participation（CozyVoice 切换至 SDK；Grafana 看面板）

### 设计决策

- SDK monorepo 内（不独立 repo）：版本同步项目节奏；CozyVoice 等下游可 `pip install -e clients/python/`
- semver 0.1.x alpha 起：API 还会基于 CozyVoice 反馈微调
- Anonymous Grafana viewer 默认开：方便临时查看；公网部署必须加 reverse proxy 鉴权
- `transcript.partial stable=true` 不在 SP4：等 STT API 调研

详见 [SP4 设计](./docs/superpowers/specs/2026-05-09-sp4-bridge-bundle-design.md) + [实施 plan](./docs/superpowers/plans/2026-05-09-sp4-bridge-bundle.md)。

---

## [0.10.0] — 2026-05-09 — SP3 Realtime Voice prompt + memory + 流式 + audit

平台化重构第四阶段：升级 realtime-server 从"单 turn 无记忆"到"多轮对话 + 流式 transcript/response + 异步 audit"。

### Added

- `services/realtime-server/app/memory.py`：ConversationMemory 滑动窗口
- `services/realtime-server/app/audit.py`：AuditWriter 异步 JSONL
- `services/realtime-server/static/index.html`：浏览器测试页
- POST /v1/sessions 加 `prompt` + `audit_persist` 入参
- WS 事件：`transcript.partial`、`response.text`、`session.update`
- WS `response.done` 加 `text` 字段（完整 assistant 回复）
- GET /info `capabilities` 加 memory / transcript_partial / response_text / default_prompt
- 5 个 env vars：RTVOICE_MEMORY_MAX_TURNS / RTVOICE_DEFAULT_PROMPT / RTVOICE_AUDIT_DIR / RTVOICE_AUDIT_QUEUE_MAX / RTVOICE_PROMPT_MAX_CHARS
- docker-compose volume：宿主 transcripts dir → /data/transcripts

### Changed

- `Session` dataclass 加 prompt / memory / audit_persist / audit_writer
- `LLMClient.stream()` 签名 `(messages: list[dict])`，pipeline 自己组消息列表
- `pipeline.run_turn`：构造 messages、emit response.text、turn 末写 memory + audit
- `STTClient` 在 main.py 创建时传 on_partial 回调（→ ws.send_json transcript.partial）

### 验证（autonomous）

- ✅ unit memory 4 测试 / audit 5 测试 / session_manager +3 / pipeline +6 / endpoints +4 / llm_client +1
- ✅ 总测试 28 → 52
- ✅ OpenAPI schema 含 prompt + audit_persist
- ✅ docker build 成功；compose validate OK
- ⏳ prod 集成测试（user-participation 浏览器验收，合并 SP2 延期项）

### 设计决策

- 滑动窗口纯 deque（O(1) append + 自动驱逐），无 tokenizer，TTFT 不退化
- audit 路径用 session 创建日期，全程一个文件（不跨 0 点切）
- session.update 仅 prompt 进白名单（YAGNI；voice/memory.clear 留 SP4+）
- LLMClient 改签名是 breaking，但 realtime-server 是 SP2 起的全新 service；agent-worker 用自己的 client copy 不受影响

详见 [SP3 设计](./docs/superpowers/specs/2026-05-09-sp3-realtime-memory-design.md) + [实施 plan](./docs/superpowers/plans/2026-05-09-sp3-realtime-memory.md)。

---

## [0.9.0] — 2026-05-08 — SP2 Realtime Voice 多 tenant session

平台化重构第三阶段：新增 `realtime-server` docker service 提供 `POST /v1/sessions` + `WS /v1/realtime/{id}` 多 tenant Realtime Voice service。

### Added

- `services/realtime-server/` 新 docker service
  - `Dockerfile` + `requirements.txt` (fastapi/uvicorn/websockets/httpx/pydantic)
  - `app/main.py` (FastAPI app + 2 endpoints + WS handler + auth)
  - `app/session_manager.py` (Session class + SessionManager + lifecycle: CREATED→ACTIVE→CLEANUP)
  - `app/pipeline.py` (run_turn: STT → LLM → TTS streaming)
  - `app/config.py` (12 个 env vars 集中)
  - `app/{stt,llm,tts}_client.py` (copy from agent-worker)
  - `app/error_schema.py` (per-service copy)
  - 4 个测试文件（config + session_manager + pipeline mock + endpoints TestClient）
- `docker-compose.yml` 加 realtime-server service block (image v0.9.0, expose 9000)
- `.env.example` SP2 段落（RTVOICE_* env vars + sizing 速查表）

### Changed

- `docs/api/sessions.md`: 状态从"协议骨架"升级为"v0.9.0 已实现"
- `docs/api/CONVENTIONS.md`: error code 表加 `session.*` `turn.*` `stt.empty` `internal.upstream_closed` 共 9 条
- `README.md` 60 秒 try 表加 Realtime API curl 示例
- `ARCHITECTURE.md` §4 Realtime Voice 段去掉"SP2 实现"占位
- `OPERATIONS.md` §2.5 SP2 env vars + §3.4 升级路径 + §4.6/4.7 故障 cookbook
- `COZYVOICE_INTEGRATION.md` §5.4 加 RealtimeClient Python SDK 完整例

### Notes / 设计决策

- `agent-worker` v0.7 LiveKit demo 路径**保留不动**进入维护模式；新 Realtime 主线在 realtime-server
- pipeline 代码 copy-paste 自 agent-worker（不抽 shared lib，与 SP1.5 决策一致）
- session_id Stripe 风格 `sess_<urlsafe-12bytes>` + Bearer + creator binding（可选）
- 协议 SP2 minimal: PCM in/out + audio.eos + transcript.final + response.done + error
- 不含 memory / prompt 透传 / transcript.partial / response.text — SP3 范围
- 所有 concurrency 参数 env-driven；3060 12GB 调优默认（cap=5）；未来 GPU 扩容只改 .env

### 验证（autonomous）

- ✅ FastAPI auto-gen `/openapi.json` 含 `/v1/sessions`
- ✅ unit test: SessionManager 11 测试全过（create/get/cleanup/capacity/expire/attach_ws）
- ✅ integration test: pipeline.run_turn() 4 mock 测试全过（happy/empty/llm 异常/finally）
- ✅ endpoints test: 11 TestClient 测试全过
- ⏳ prod 集成测试（待 user 部署 + 浏览器对话验收）

详见 [SP2 设计文档](./docs/superpowers/specs/2026-05-08-sp2-realtime-session-design.md) + [实施 plan](./docs/superpowers/plans/2026-05-08-sp2-realtime-session.md)。

---

## [0.8.0] — 2026-05-08 — SP1.5 API 规范 + endpoint refactor

平台化重构第二阶段：API 路径加 `/v1/` 前缀、错误格式统一、写完整 API 规范。

### Added

- `docs/api/CONVENTIONS.md` — 13 章节 API 规范（路径风格 / 版本 / 错误格式 / 鉴权 / headers / capability discovery / deprecation 流程 / 现有 endpoint 迁移表）
- `docs/api/stt.md` `tts.md` `sessions.md` — 每 service 完整 API 文档（含 WS 协议描述、error codes、Python+Node 例子）
- 各 service 加 `app/error_schema.py`：`ErrorResponse` Pydantic + `api_error()` helper + `http_exception_handler()`

### Changed (BREAKING)

| 老路径 | 新路径 |
|---|---|
| `WS /asr` | `WS /v1/asr` |
| `POST /tts/stream` | `POST /v1/tts/stream` |
| `WS /tts/stream_ws` | `WS /v1/tts/stream_ws` |
| `GET /voices` | `GET /v1/voices` |
| `POST /voices/add` | `POST /v1/voices` (RESTful collection，去掉 verb) |
| `DELETE /voices/{spk_id}` | `DELETE /v1/voices/{spk_id}` |
| `POST /token` | `POST /v1/tokens` (单数→复数) |

**所有 4xx/5xx 响应 body** 改为 `{type:"error",code:"<service>.<reason>",message,request_id}`。

`/health` `/metrics` `/info` `/openapi.json` 保持原路径（运维面无版本）。

### Deprecated / Removed

老路径**直接删**（hard cutover；CozyVoice 未接入，无外部 consumer）。

未来 breaking change 改为软迁移：response 加 `Deprecation: true` + `Sunset: <RFC HTTP date>`，≥1 release 周期后才返 410。

### Notes

- agent-worker `STT_WS_URL` 默认值 + `tts_client.py` hardcoded 路径同步改 `/v1/`
- `services/token-server/static/index.html` 改 `/token` → `/v1/tokens`
- 跨文档（README / ARCHITECTURE / OPERATIONS / COZYVOICE_INTEGRATION）所有 endpoint 引用同步更新

详见 [SP1.5 设计文档](./docs/superpowers/specs/2026-05-08-sp1.5-api-conventions-design.md) + [实施 plan](./docs/superpowers/plans/2026-05-08-sp1.5-api-conventions.md)。

### 验证（autonomous）

- ✅ FastAPI auto-gen `/openapi.json` 每 service 含新路径
- ✅ 沙盒 mock test：3 个 tts main_*.py + stt-server + token-server 路径 routes 验证
- ✅ 全文档链接 lint 0 [FAIL]
- ⏳ prod 集成测试 + 浏览器对话验收（待 user 在 SP1.5 完工后做）

---

## [0.7.1] — 2026-05-07 — `8ed9a4d` `45fbf83` `c7d4556`

Build 性能优化：Dockerfile 层重排让 code-only rebuild 从 ~3.5min 降到 ~1s（240×）。同步把 v0.6 Dockerfile.cosyvoice 也改了，回滚后享受同优化。

### Added
- `OPERATIONS.md` §8 Build 性能 & Docker 缓存（54 行）：
  - 黄金法则：易变层放后面、重型操作放前面
  - 实测对比表（215s → 1s）
  - BuildKit content-hash 陷阱：`touch` 不触发 cache miss，必须改文件内容
  - chown -R 在 60GB venv 上 = 215s 的真实数字
  - 不要主动 prune build cache（用 `--keep-storage` 留水位）

### Changed
- `services/tts-server/Dockerfile.cosyvoice3` (`8ed9a4d`)：
  - useradd + `chown -R /opt/venv /opt/CosyVoice` 提前到 COPY app 之前（缓存稳定）
  - COPY 用 `--chown=appuser:appuser` 内联设置 ownership
- `services/tts-server/Dockerfile.cosyvoice` (`45fbf83`)：v0.6 同样重排，与 v0.7.1 对齐

### Notes
- BuildKit (Docker 23+) 用 content-hash 而非 mtime 判 cache 失效——从老 docker build 思维迁移时常见误诊源（`touch` 不触发 cache miss）。测优化时必须改文件内容（`echo / sed -i`）。
- 这种"格式上一样、效果重大不同"的改动 transitive trust 合理：v0.7.1 已实测（1s code-only rebuild），v0.6 同 pattern 推断同效。
- v0.6 改动是 preventive maintenance，prod 当前跑 v0.7 不影响；用户某天回滚 v0.6 + 改 prompt 后 rebuild 时享受。

### 验证（autonomous，prod GPU）
- ✅ Dockerfile 重排后首次 rebuild 8.75 min（chown 跑一次）
- ✅ 无改动 rebuild 1s（cache 全命中）
- ✅ 真改动 rebuild 1s（cache 在新位置全命中）

---

## [0.7-fix-1] — 2026-05-07 — `da78e29`

prod 实测发现 bug：CosyVoice 3 LLM 硬要求输入序列含 `<|endofprompt|>` token (151646)。

### Fixed
- `services/tts-server/app/main_cosyvoice3.py`：`DEFAULT_PROMPT_TEXT` 末尾加 `<|endofprompt|>`：
  ```python
  # 之前
  DEFAULT_PROMPT_TEXT = "希望你以后能够做的比我还好呦。"
  # 之后
  DEFAULT_PROMPT_TEXT = "希望你以后能够做的比我还好呦。<|endofprompt|>"
  ```

### Notes
- 触发条件：v0.7.0 prod 启动后 `/tts/stream` 第一次合成请求 hang 60s → 看 tts-server 日志才发现 LLM thread crash:
  ```
  AssertionError: <|endofprompt|> not detected in CosyVoice3 text or prompt_text
  ```
- 根因：`cosyvoice/llm/llm.py:479` 在 v3 LLM `inference()` 里硬断言 token 151646 必须在输入序列中。v3 frontend 的 `text_normalize` / `_extract_text_token` 都不自动添加；caller 必须显式拼。v2 不要求此 token。这是**undocumented contract** —— v3 README/runtime demo 都没写明，只在 `cosyvoice/utils/common.py` 方言指令模板里能看出该 token 的用法。
- 沙盒 mock 测试覆盖不到：`FakeCosyVoice3` 在我们的协议测试里不真跑 LLM forward，断言不触发。**必须 prod GPU 实测**才暴露——OPERATIONS.md §3.2 早就标"真 CosyVoice 3 推理性能 → prod GPU 实测" ⏳，一发即中。

### 升级影响
- 需 push + git pull + rebuild tts-server（image 重做小步骤，~6 min，包含 chown -R 60GB 一次）
- 已纳入 [PROD_VALIDATION.md §3](./PROD_VALIDATION.md)

---

## [0.7.0] — 2026-05-06 — `af919a4` `b658573` `c54f963`

Fun-CosyVoice 3 GPU 变体，与 v0.6 (CosyVoice 2) 并存；新增 WebSocket 双向流式协议。

### Added
- `services/tts-server/Dockerfile.cosyvoice3` + `app/main_cosyvoice3.py` + `entrypoint-cosyvoice3.sh`
  - 模型：`FunAudioLLM/Fun-CosyVoice3-0.5B-2512`（~5.6GB，跳过 `llm.rl.pt`）
  - 类切换 `CosyVoice2` → `CosyVoice3`；构造器去掉 `load_jit`；其余公开 API 不变
  - admin endpoints (POST/DELETE /voices) Q2 全套继承
- WebSocket `/tts/stream_ws`（仅 v0.7 端点）
  - 协议：首帧 JSON metadata → 文本增量帧 N 个 → "EOS"
  - 服务端 binary PCM chunks + 末帧 `{"type":"done"}`
  - 三路 Bearer 鉴权与 STT 一致
  - 同步 generator 桥（async ws msg → sync queue → CosyVoice3 → async pcm queue）
- `/info` 加 `text_streaming: true` 让 agent-worker 自动检测能力
- `tts_client.py::open_ws()` + `TTSWSStream` 类（send_text / eos / audio_chunks / aclose）
- agent-worker `_run_pipeline_ws()`：能力探测命中即走 ws 路径；失败本轮放弃下轮重试
- `.env.example` 加 v0.7 切换文档（TTS_DOCKERFILE / TTS_IMAGE / TTS_MEM_LIMIT / SKIP_RL_MODEL）

### Changed
- 协议层向后兼容：v0.6 `POST /tts/stream` 仍然保留，agent 通过 capability 探测选择路径

### Fixed (`c54f963`)
- 服务端 `/tts/stream_ws`：client abrupt close 后 `send_bytes` 抛 `RuntimeError`（不是 `WebSocketDisconnect`），原 except 没接住 → 循环不 break。修复：合并捕获两种异常。
- 客户端 barge-in：`finally` 里 `await ws.aclose()` 在外层 task.cancel 时被中断 → close frame 发不出 → server 浪费 GPU。修复：`asyncio.shield(asyncio.wait_for(aclose, timeout=2))`。

### Notes
- v0.7 与 v0.6 镜像 / named volume 各自独立，**回滚秒级**（删 .env 三行 + restart）
- WebSocket 设计选择：metadata 用 JSON、文本块纯文本、控制信号 sentinel "EOS"。三种语义共用 text frame，靠"首帧必须 JSON" + "EOS 三字符"区分。简单且 wireshark 友好。
- CosyVoice 3 内部支持 `tts_text=Generator`，所以未来 agent 把 LLM token 流直接当 generator 传入即可享受 150ms 真端到端，不需要再改 server。
- API 不变 + 引擎重写：协议层零工作量，仅 5 处 mechanical 修改。

### 验证（autonomous）
- ✅ 5 条 ws 协议断言（/info、no-auth handshake reject、happy path、subprotocol、bad voice）
- ✅ 2 条 barge-in 场景（abrupt close mid-stream + 立即 recovery 不 crash）
- ✅ 11 条 admin endpoint 回归（与 v0.6 等价）
- ⏳ 真 CosyVoice 3 推理性能 + 150ms 端到端延迟 + TensorRT 10.13 pin 兼容性 → prod GPU 实测

---

## [0.6.2] — 2026-05-06 — `4ad21fc` `82b8628` `3fc9fa1`

容错矩阵收尾批次：LLM 流式硬化 + STT 自动重连 + OPERATIONS.md 文档。

### Added
- LLM 客户端三类硬化（`4ad21fc`）：
  - `httpx.Timeout(connect=10, read=30)` 替代 SDK 默认；`read` 在流式下天然变 per-chunk timeout
  - 0 token 回复（连接成功但 LLM 没说话）→ yield `LLM_FALLBACK_REPLY` 让 agent 不沉默
  - 已发部分 token 后异常 → 截断（不拼 fallback，避免半句续接很怪）
  - 新 env：`LLM_CONNECT_TIMEOUT_S` / `LLM_READ_TIMEOUT_S` / `LLM_FALLBACK_REPLY`
- STT 客户端长连接自愈（`82b8628`）：
  - 初次 connect：5 次指数退避（1→2→4→8→16s）
  - reader 检测 ConnectionClosed → finally 调度后台重连任务（asyncio lock 单飞）
  - 重连期 feed 静默 drop、request_final 立刻返空串
  - close() 先停 reconnect 再断 ws
  - 新 env：`STT_CONNECT_RETRIES` / `STT_CONNECT_BACKOFF_INITIAL_S` / `STT_CONNECT_BACKOFF_MAX_S`
- `OPERATIONS.md` 263 行（`3fc9fa1`）：容错矩阵 + 环境变量速查 + 升级路径 + 排障 cookbook

### Notes
- "agent silently goes deaf" 是 STT 长连接最致命的失败模式；必须有 reconnect。LLM/TTS 短期 RPC 单次重试就够，STT 必须 reconnect loop。
- 重连后 sherpa-onnx 是新 stream → 当前 utterance 数据丢失。这是用"丢局部"换"全局可用性"的合理权衡：用户被 agent 没听见会自然重复，比"半句拼半句"产出乱七八糟好得多。
- 错误恢复有时不是叠加而是**停止**：半句"今天天气"后接"抱歉没听清"会让用户更困惑；保持半句让对方自然要重复才是更好的对话恢复。
- httpx `read` timeout 用作 per-chunk timeout 比手写 `asyncio.wait_for(__anext__)` 优雅——底层 socket 已在做这个监控。

### 验证（autonomous）
- ✅ 5 条 LLM 硬化场景（happy / empty / connect-error / mid-stream-error / cancel）
- ✅ 4 条 STT 重连场景（handshake-retry / drop+reconnect / feed-during-reconnect / total-failure-raises）
- ⏳ prod 真实容器抖动场景下重连/timeout 的实际触发率与日志噪音

---

## [0.6.1] — 2026-05-06 — `2ce1621` `4bff9f0` `5da290a`

"v0.6 三件套"：LLM prompt 提到 env、CosyVoice voice clone admin API、STT/TTS 对外鉴权 + TLS 模板。让 RTVoice 能给"另一个 RealTime 项目"调用，并允许在不 rebuild 镜像的情况下定制对话风格 / 声音。

### Added — Q1 LLM prompt env-driven (`2ce1621`)
- `llm_client.py`：`SYSTEM_PROMPT` 改读 `AGENT_SYSTEM_PROMPT` env，留空走默认 30 字短回复
- `AGENT_LLM_MAX_TOKENS` env（默认 80）控制 LLM 上限
- `docker-compose.yml` 透传两个 env；改 prompt 只需改 .env + restart agent-worker

### Added — Q2 CosyVoice voice clone admin API (`4bff9f0`)
- `POST /voices/add`：multipart 上传 reference wav + spk_id + prompt_text → `add_zero_shot_spk` + `save_spkinfo` 持久化
- `DELETE /voices/{spk_id}`：默认音色保护；删除时同步 spk2info.pt + voices/<id>.wav
- `TTS_ADMIN_API_KEY` Bearer 鉴权（留空 = endpoints 禁用）
- 原始 wav 另存到 `voices/` 子目录便于审计/重建（同 named volume）
- spk_id 路径穿越防护 + wav 大小上限（5MB 默认）
- `requirements.cosyvoice.txt` 加 `python-multipart`

### Added — Q3 Bearer auth + TLS 模板 (`5da290a`)
- `RTVOICE_API_KEY` 统一鉴权 STT/TTS：
  - STT WS：三路 Bearer（Authorization header / `Sec-WebSocket-Protocol: bearer.<KEY>` / `?token=<KEY>` 兜底），失败 close code 4401
  - TTS HTTP：`/tts/stream` + `/voices` 加 FastAPI Depends 校验
  - agent-worker 的 STTClient/TTSClient 配套传 api_key
  - 留空 = 鉴权关闭（dev 默认）；prod 公网暴露必填
- `docker-compose.api.yml` override：把 STT 9090 + TTS 9880 bind 到宿主
- `docker-compose.tls.yml` + `caddy/Caddyfile`：Caddy 反代 + 自动 TLS（公网 LE 或内网 'tls internal' 自签）

### Notes
- 这个 v0.6.1 的核心目标用户是"另一个项目"复用 STT/TTS 引擎
- `/voices/add` 用 spk2info.pt 持久化走 CosyVoice 内置 `save_spkinfo()`（写 model dir = named volume），重启自动 reload
- WS 三路 Bearer 因为浏览器不能轻易加 header；subprotocol 是 WebSocket 标准字段不会被中间代理 strip

### 验证（autonomous）
- ✅ 11 条 admin endpoint 断言（auth / 校验 / add / delete / wav cleanup）
- ✅ 6 条 HTTP auth 断言（无 token / 错 token / 正确 token × 3 个端点）
- ✅ 4 条 STT WS auth 断言（header / subprotocol / query / empty bypass）
- ⏳ Caddy TLS 在公网域名下的 cert auto-renew

---

## [Unreleased] / Engineering — 2026-04-30 — `5dab7d6` `6056f24` `260ebed`

"v0.5+ 工程化补完"批次 — 用户要求"自主完成尽可能多的工作"，三个并行批 commit。

### Phase A — `5dab7d6` 生产部署脚手架
- `docker-compose.prod.yml`
  - llm-server 完全替换为 `vllm/vllm-openai:v0.7.0` + Qwen2.5-3B-GPTQ-Int4
  - stt-server 切到 GPU 镜像 + STT_PROVIDER=cuda
  - tts-server 暂仍 Kokoro CPU（CosyVoice 2 留 v0.6）
  - `${VAR:?}` 强制必填变量（缺 BIND_HOST/LIVEKIT_PUBLIC_URL 拒绝启动）
- `livekit/livekit.prod.yaml`：`use_external_ip=true` + UDP 50000-50099 + JSON log
- `scripts/prod-deploy.sh`：落地 [SECURITY.md §4](./SECURITY.md) 4 阶段协议
  - `--inspect` / `--backup` / `--apply` 分步
  - 拒绝 .env 中 `*changeme*` / `*devsecret*` 默认值
  - 单服务渐进部署 + healthcheck 等待 + 二次确认
- `scripts/backup-volumes.sh`：alpine ro 挂载打 tar.gz
- `services/stt-server/Dockerfile.gpu` + `requirements.gpu.txt`：CUDA 12.4 + onnxruntime-gpu
- `stt-server/app/main.py`：provider 改为环境变量驱动（cpu / cuda）

### Phase B — `6056f24` dev 阶段补强
- token-server 加 slowapi rate limit @ /token（默认 30/min/IP）
- agent-worker 心跳文件健康检查（替换 v0.2 留的 `HEALTHCHECK NONE`）
  - `agent.heartbeat_loop` 每 5s touch /tmp/agent-heartbeat
  - Dockerfile HEALTHCHECK 看 mtime（30s 内 healthy）
- 删除已废弃的 `mock_pipeline.py`（v0.5+ 全切真引擎）
- README.md 加 Quick Start 章节
- CONTRIBUTING.md 贡献指南
- .env.example 大改：分节 [必改] / dev / prod 注释清晰

### Phase F — v0.6 (experimental) livekit-agents 框架迁移
**并行实现**，不破坏 v0.5.1。通过 `AGENT_BACKEND=v06` opt-in。

新增：
- `app/plugins.py` — 自定义 STT/TTS plugin 子类
  - `RTVoiceSTT(stt.STT)` + `_RTVoiceRecognizeStream(stt.RecognizeStream)`
    包装现有 `STTClient` WS 协议
  - `RTVoiceTTS(tts.TTS)` + `_RTVoiceChunkedStream(tts.ChunkedStream)`
    包装现有 tts-server HTTP chunked 协议
- `app/main_v06.py` — `cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))` 入口
- Dockerfile CMD 按 `AGENT_BACKEND` 分支：默认 v0.5.1，opt-in v0.6
- docker-compose / .env.example 加 `AGENT_BACKEND` 透传

依赖（pip 协商替代精确 pin）：
- `livekit-agents>=1.5.6,<2.0`
- `livekit-plugins-openai>=1.5.6,<2.0`
- `livekit-plugins-silero>=1.5.6,<2.0`
- 镜像从 537MB → 742MB（+200MB framework）

模块映射 (v0.5.1 → v0.6)：
- 自写 5 状态 FSM → `AgentSession` 内置 turn detection
- 手写 onnxruntime VAD → `silero.VAD.load()` plugin
- `llm_client.py` → `openai.LLM(base_url=ollama/vLLM)` 直接（无需自定义）
- `phrase_split.py` + 手写 producer/consumer → framework 自动

⭐ 自动验证（超预期）：
- ✅ 镜像 build + import + 实例化全过
- ✅ **worker 实际注册到 LiveKit**：日志 `"registered worker" agent_name=rtvoice-agent url=ws://livekit-server:7880 protocol=17`
- ⏳ entrypoint 实际触发 / 端到端对话需 home box 真音频验证

详见 [docs/v0.6-validation.md](./docs/v0.6-validation.md)。

### Phase E — Grafana 监控栈
基于 Phase D 暴露的指标，提供 opt-in 的 prometheus + grafana 栈。

- `monitoring/prometheus.yml` — 5 个 scrape job（含自身），15s interval
- `monitoring/grafana-provisioning/` — datasource + dashboards 自动加载
- `monitoring/dashboards/rtvoice.json` — RTVoice 总览 dashboard
  - 21 panels / 5 rows：Global Health / Agent E2E / STT / TTS / Token Server
  - 关键 panel：Agent FSM Gauge / round_seconds p50p95 / first_audio p50p95 /
    TTS phrase RTF（红线 < 1.0）/ HTTP latency
  - 引用 20 个指标，全对应 Phase D 实际暴露
- `docker-compose.monitoring.yml` — 可选 overlay
  - `--profile monitoring` 启 prometheus（13000→3000 避端口冲突）+ grafana
  - 镜像：`prom/prometheus:v2.55.0` + `grafana/grafana:11.4.0`
  - named volume 持久化 TSDB 与 Grafana 配置
- `monitoring/README.md` — 启动 / 自定义 / 接现有 Prometheus / 未来 alerting 计划

autonomous 验证：
- ✅ 5 targets 全 UP（含 prometheus 自己）
- ✅ Grafana 自动加载 dashboard `rtvoice-overview` 21 panels
- ✅ PromQL `rtvoice_agent_state` 返回正确 FSM 状态
- ✅ Grafana 端口冲突（3000 → 13000）已处理

### Phase D — Prometheus 指标
所有 4 个 service 暴露 `/metrics`（agent-worker 在独立端口 :9100），
Prometheus 文本格式，可直接对接 Grafana/Alertmanager。

- token-server (`/metrics`)
  - `http_request_duration_seconds`（自动）
  - `rtvoice_tokens_issued_total{room}`
  - `rtvoice_token_auth_failures_total{reason}` (missing / invalid)
- stt-server (`/metrics`)
  - `rtvoice_stt_ws_connections_active|total`
  - `rtvoice_stt_events_total{type}` (partial / final_eos / final_endpoint)
  - `rtvoice_stt_decode_seconds` (sherpa-onnx decode_stream 延迟直方图)
- tts-server (`/metrics`)
  - `rtvoice_tts_phrases_total / failures_total`
  - `rtvoice_tts_ttfb_seconds` (首包延迟)
  - `rtvoice_tts_phrase_rtf` (per-phrase real-time factor)
- agent-worker (`:9100/metrics`)
  - `rtvoice_agent_state{state}` (idle / listening / thinking / speaking / interrupted Gauge)
  - `rtvoice_agent_rounds_total / barge_ins_total / pipeline_errors_total`
  - `rtvoice_agent_round_seconds / round_phrases / first_audio_seconds` (Histograms)
  - `rtvoice_agent_stt_finals_total / stt_partials_total`

新增依赖：`prometheus-client==0.21.1` / `prometheus-fastapi-instrumentator==7.0.2`

新增模块：`services/agent-worker/app/metrics.py`

### Phase C — `260ebed` 测试 + CI
- 集成测试脚本：`scripts/test-stt.sh` `test-llm.sh` `test-tts.sh`
- pytest 单元测试：
  - `test_phrase_split.py` 11 用例
  - `test_state_machine.py` 7 用例
  - 18/18 passing
- GitHub Actions：
  - `.github/workflows/lint.yml`：yamllint + shellcheck + hadolint + compose config
  - `.github/workflows/pytest.yml`：path filter agent-worker 改动触发
- `docs/benchmarks/template.md`：实测报告模板

---

## [0.5.1] — 2026-04-30 — `c05b41f`

LLM 流式句切分 → 并发 TTS pipeline 顺序播放（首包延迟优化）

### Added
- `services/agent-worker/app/phrase_split.py` — LLM token 流 → phrase async generator
  - 硬标点（`。！？.!?\n`）+ MIN_LEN=4
  - 软标点（`，；：,;:`）+ SOFT_MIN_LEN=8（避免太碎）
  - 长度兜底 MAX_LEN=40
- `_run_pipeline` 重写为 producer / consumer 模式
  - producer：LLM stream → split → `asyncio.create_task(synth)` 即切即送
  - consumer：严格按入队顺序 await → publish PCM
  - `Semaphore(TTS_PIPELINE_CONCURRENCY)` 限并发
- 第一个 phrase 就绪即 `THINKING → SPEAKING`（开播）
- `[ROUND METRIC]` 每轮日志：`phrases / first_phrase_ready_ms / first_audio_ms / round_ms`
- `TTS_PIPELINE_CONCURRENCY` 环境变量（默认 2）

### Changed
- 拆分 `_publish_pcm_bytes` 与 `_synth_phrase` 为独立方法
- agent banner 升级：`v0.5.1`，含 `pipeline_concurrency=2`

### Notes
- **首包延迟节省**（理论）：从 `LLM_full + TTS_first`（v0.5）降到 `LLM_first_phrase + TTS_first`（v0.5.1），典型 1-3s
- **CPU bound 时 concurrency 不带来并行收益**：dev 沙盒 RTF<1，TTS 任务串行排队即可
- **真正的省时间在"早发"**，不在"并行"——这是 voice agent 流式优化的核心

### 验证
- ✅ autonomous：phrase_split 9 用例全过（短句 / 多硬标点 / token 级 / 软标点过短不切 / 长度兜底 / 标点紧贴 / 中英混合 / 真实 LLM 样本）
- ✅ autonomous：agent 启动 banner v0.5.1，`pipeline_concurrency=2`
- ⏳ 人工：home box 上看 `[ROUND METRIC]` 实测延迟数据

---

## [0.5] — 2026-04-30 — `83c787f`

TTS 切真：Kokoro 82M ONNX CPU。dev 阶段三引擎全部脱离 mock。

### Added
- **新服务 `tts-server`**：FastAPI + kokoro-onnx 0.5.0
  - 模型：Kokoro v1.0（325MB 推理 + 28MB 音色）
  - 54 个音色，默认 `zf_xiaobei`（中文女声，espeak-ng `cmn`）
  - HTTP 协议：`POST /tts/stream` chunked PCM int16 LE 24kHz mono
  - 句级流式：按中英标点切短语，逐句合成逐句推
  - 辅助端点：`/health` `/info` `/voices`
  - 镜像 1.93GB（含模型）
- `services/agent-worker/app/tts_client.py` — httpx 异步流式 HTTP 客户端
- agent `_stream_tts_to_room` — 把变长 chunk 切成 480 samples/帧 publish
- `httpx==0.27.2` 依赖
- agent 输出 `AudioSource` 升至 24kHz（与 Kokoro 原生对齐，避免重采样）
- `TTS_VOICE` `TTS_LANG` `TTS_BASE_URL` 环境变量
- `docs/v0.5-validation.md` — 性能现实文档

### Changed
- agent banner 升级 `v0.5`，输出全部 4 个客户端配置
- `mock_pipeline.py` 中 `mock_tts` 不再被 `_run_pipeline` 调用（保留代码至 v0.6 删除）

### Notes
- **kokoro-onnx 选型理由**：纯 ONNX 不拖 torch（继 v0.2 silero-vad 教训）
- **CosyVoice 2 CPU 不可行**：5-10× realtime 慢，破坏流式承诺；改用 Kokoro
- **dev 沙盒（Xeon E5-2697 v2 @ 2.7GHz, 2013）实测 RTF ≈ 0.1-0.2×**
  - 与方案设计无关：CPU 无 AVX2 优化
  - 现代 CPU 应可 1-2× realtime；用户家 home box 是真实 dev 基线
  - 生产 v0.5+ 切 GPU 后用 CosyVoice 2

### 验证
- ✅ autonomous：tts-server build + 模型加载 + 54 音色就绪
- ✅ autonomous：HTTP `/tts/stream` 协议正确（chunked PCM, X-Sample-Rate=24000）
- ✅ autonomous：agent v0.5 启动后 4 client 全连接
- ⚠️ autonomous：性能 < 1× realtime（沙盒 CPU 限制）
- ⏳ 人工：home box 上听感与延迟

---

## [0.4] — 2026-04-30 — `9ece9d0`

LLM 切真：ollama + Qwen2.5-1.5B CPU，OpenAI 兼容流式 API。

### Added
- **新服务 `llm-server`**：FROM `ollama/ollama:0.22.0` + 自定义 entrypoint
  - 启动时 `ollama serve` + 自动 pull 配置的 `LLM_MODEL`
  - Qwen2.5-1.5B Q4_K_M（~1GB），4 核 CPU 跑得动
  - OpenAI 兼容端点 `/v1/chat/completions`（POST + SSE 流式）
  - healthcheck：模型已 pull + ollama 可响应（start_period 5min）
  - 资源上限 4GB / 4 cpus
  - 数据卷 `rtvoice_ollama_models`（首次 pull 后持久）
- `services/agent-worker/app/llm_client.py` — `openai.AsyncOpenAI` 包装
  - 系统提示词强制中文短句、无 emoji（语音友好）
  - `stream(text)` 异步生成 token deltas
- `services/llm-server/entrypoint.sh` — auto-pull 启动脚本
- `openai==1.59.7` 依赖
- `LLM_BASE_URL` `LLM_MODEL` `LLM_API_KEY` `OLLAMA_KEEP_ALIVE` 环境变量

### Changed
- `mock_pipeline.mock_tts` 修正：处理多字符 chunk（OpenAI 流式常见 1-3 char/chunk）
- agent `_run_pipeline`：累积 LLM token 成完整文本再 POST TTS（v0.5.1 改为流式）

### Fixed
- `.env` 中老的 `LLM_MODEL=Qwen/Qwen2.5-3B-Instruct` 被新代码读到（HF 风格名 ollama 拉不到）—— 改为 `qwen2.5:1.5b`

### Notes
- **OpenAI 兼容 API 是 LLM 切换的"通用快速接口"**：今天 ollama，v0.5+ vLLM，代码零改动
- **ollama 镜像 3.77GB**：自带 CUDA libs，dev 浪费空间但 prod GPU 切换零阻力
- **".env 已存在 → .env.example 改了等于没改"陷阱**：新增配置项要同步检查 `.env`

### 验证
- ✅ autonomous：模型自动 pull (~45s)，`ollama list` 显示 `qwen2.5:1.5b 986MB`
- ✅ autonomous：`POST /v1/chat/completions` 流式 SSE，
  示例 "你好，今天天气怎么样？" → "抱歉，我没有实时获取天气信息的能力。..."
- ✅ autonomous：agent v0.4 启动后 STT + LLM 双连接成功
- ⏳ 人工：真实对话质量

---

## [0.3] — 2026-04-30 — `31dc7a3`

STT 切真：sherpa-onnx Streaming Zipformer 中文 CPU，WS 协议。

### Added
- **新服务 `stt-server`**：FastAPI + `sherpa-onnx==1.13.0`
  - 模型：`sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20`（int8）
    - encoder 181.9MB + decoder 13.1MB + joiner 3.2MB
  - WS 协议 `/asr`：bytes=PCM int16 LE 16kHz / text="EOS"|"RESET"
    - JSON events：`{type: partial|final|error, text}`
  - 端点检测兜底（agent 已有 VAD 主导 turn 边界）
  - HTTP `/health` `/info`
  - 镜像 1.14GB（其中 ~200MB 模型）
- `services/agent-worker/app/stt_client.py` — WS 长连接客户端
  - reader task 后台收 partial/final 事件
  - `feed(pcm)` 不阻塞，`request_final(timeout)` 发 EOS 等 final
  - barge-in 时调 `reset()` 丢弃 stream 状态
- agent LISTENING 状态下持续 `_enqueue_stt` → 后台 feeder 推 PCM
- `websockets==13.1` 依赖
- `STT_WS_URL` `STT_FINAL_TIMEOUT_S` 环境变量

### Changed
- 选型变化：原 `ENGINES.md §2` 计划 Paraformer streaming，但其 HF 仓库匿名下载 401
  → 切到同档备选 Zipformer streaming（公开可下载，协议兼容，零代码改动）
- 兼容 `sherpa-onnx 1.13` 新 API：`get_result()` 直接返 `str`（旧版返对象）

### Notes
- **HF 模型下载比 GitHub releases 标准**：sherpa-onnx 把模型从 GH releases 迁到 HF（GH LFS 收费）
- **Zipformer = Paraformer 同档替代**：rubric-driven 选型让此切换无痛——若钉死 Paraformer 就需修改 ADR 决策
- **WS 在 docker network 内几乎零开销**：握手 < 5ms，bytes 透传

### 验证
- ✅ autonomous：用模型自带 test_wavs 实测
  - `0.wav`（中英混合 10s）→ `"昨天是 MONDAY"` ✅
  - `1.wav`（中英混合 5s）→ `"这是第一种第二种叫呃与 ALWAYS ALWAYS什么"` ✅
- ✅ autonomous：4-9 个 partial events / 段
- ✅ autonomous：agent v0.3 启动 STT WS 握手成功（HTTP 101）
- ⏳ 人工：真音频端到端（playwright 无 mic）

---

## [0.2] — 2026-04-29 — `64957e6`, `f3bc566`

agent-worker 加入 LiveKit + 自写状态机 + silero VAD + mock STT/LLM/TTS（in-process）。

### Added
- **新服务 `agent-worker`**：低层 `livekit-rtc` + 自写状态机
  - `livekit==1.0.16` (rtc) + `livekit-api==1.1.0`
  - **不用 `livekit-agents` 框架**：v0.2 mock 阶段无真引擎可填，框架反而是空壳
  - v0.6 计划迁移
- `app/state_machine.py` — 5 状态 FSM
  - `Idle / Listening / Thinking / Speaking / Interrupted`
  - 合法转移检查表 + on_change 回调
- `app/vad.py` — silero VAD ONNX 直加载
  - 走 `onnxruntime`，**不用 `silero-vad` pip 包**（pip 版拖 torch + CUDA toolkit ~12GB ⚠️）
  - 32ms 帧粒度，VAD speech_start / speech_end 检测
- `app/mock_pipeline.py` — mock STT (canned)，mock LLM (5 句关键词)，mock TTS (sine wave 24kHz)
- agent 自签 LiveKit JWT 加入 room（不通过 token-server）
- 浏览器测试页升级：mic 按钮 / remote 音频自动播放 / 4 状态 badge
- `livekit-client@2.18.7` CDN
- `AGENT_ROOM` `AGENT_IDENTITY` 环境变量
- `docs/v0.2-validation.md` — 自动验证报告 + 人工验证步骤

### Fixed
- `f3bc566`：agent-worker healthcheck 暂禁用（python:3.11-slim 不带 procps，pgrep 永远 unhealthy）
  - v0.3+ 改用心跳文件方案；当前 `restart: unless-stopped` 兜底足够

### Notes
- **silero-vad pip 包陷阱**：即使 `onnx=True` 仍拖 torch + CUDA libs ~12GB → docker layer extract 失败
  - 解决：直接下载 silero VAD ONNX (~2MB) + onnxruntime 直加载
  - 镜像从 build 失败 → 535MB
  - 教训写入 memory：未来选音频/AI 包前先看依赖图
- **不用 livekit-agents 框架的决策**：mock 阶段填不满 plugin 接口；v0.6 真引擎到位再迁移

### 验证
- ✅ autonomous：agent join room、publish track、订阅用户音频
- ✅ autonomous：浏览器 ↔ agent 双向 track 建立
- ⚠️ autonomous：VAD/状态机/barge-in 需真音频（playwright 无 mic，silero 拒绝合成音）
- ⏳ 人工：完整 mock 对话循环 + barge-in 测试

---

## [0.1] — 2026-04-29 — `090d258`

LiveKit + token-server，浏览器可加入房间（最小可验证骨架）。

### Added
- **新服务 `livekit-server`**：`livekit/livekit-server:v1.11.0`
  - `livekit/livekit.dev.yaml` 配置（端口 7880/7881，UDP 50000-50009 dev / 50000-50099 prod）
  - 端口绑 `127.0.0.1`（dev 安全默认）
- **新服务 `token-server`**：FastAPI + `livekit-api==0.8.2`
  - `python:3.11-slim` 非 root 容器
  - HTTP `/token` 接口签发 LiveKit JWT
  - **Bearer 鉴权**：`Authorization: Bearer <APP_API_KEY>`，`hmac.compare_digest` 防 timing attack
  - `DEV_AUTO_INJECT_KEY=true` 时把 key 注入测试页 meta（仅 127.0.0.1 安全）
  - `static/index.html` 嵌 `livekit-client@2.18.7` 测试页
- `scripts/dev-up.sh` 含 .env 校验（拒绝默认弱 key）
- `scripts/dev-down.sh --wipe` 需输入 `YES I AM SURE` 二次确认
- `APP_API_KEY` 环境变量（≥32 字符）
- 上线 GitHub: `git@github.com:zinohome/RTVoice.git`

### 验证
- ✅ autonomous：6 个端点 + 协议（健康 / 无 auth 401 / 错误 auth 401 / 正确 auth 200）
- ✅ autonomous：浏览器 → token-server → LiveKit 握手成功（playwright）
- ✅ autonomous：服务端日志确认 `room=rtvoice-test participant=user-1 joinDuration=330ms`
- ✅ autonomous：12 项 SECURITY.md 红线检查全过（端口绑 127、镜像 pin、非 root、卷命名等）

---

## [0.0] — 2026-04-29 — `a8c25c2`, `8201eb3`

文档与脚手架立项。**未启动任何服务**。

### Added
- `SECURITY.md` — 9 节安全契约（红线 + 软约束 + 4 阶段生产迁移协议）
- `DEPLOY.md` — dev/prod 环境矩阵 + 部署流程 + 备份恢复 + 故障排查
- `ARCHITECTURE.md` — 12 节系统设计
  - 设计目标（端到端 ≤1.2s p95，单卡 RTX 3060 12GB）
  - 组件全景 + 数据流（含 mermaid 图）
  - 关键时序：正常对话 / barge-in / 错误（mermaid sequence）
  - 5 状态 FSM + 不变式
  - 性能预算（端到端 1.0s / 10GB 显存）
  - 失败模式 + 5 条 ADR 决策记录
- `ENGINES.md` — STT/TTS/LLM 候选评估 + 选型理由
  - 8 维 rubric（流式延迟/中文质量/显存/吞吐/生态/可定制/许可/维护）
  - STT：6 候选 → sherpa-onnx + Paraformer/Zipformer
  - TTS：9 候选 → CosyVoice 2 0.5B（prod）+ Kokoro（dev）
  - LLM：8 模型 + 5 后端 → Qwen2.5-3B + vLLM（prod）/ Qwen2.5-1.5B + ollama（dev）
  - 已排除方案清单（防后人重复争论）
- `README.md` `.gitignore` `.env.example`
- 目录脚手架：`services/{token-server,agent-worker,stt-server,tts-server}/` 各带 README
- `docs/` `livekit/` `scripts/` 占位 README

### Notes
- **决策**：用户极度看重生产隔离 → 先文档后代码
- **环境差异显式化**：`docker-compose.yml + dev.yml + prod.yml` profile 切换
- 整套契约写入 memory：未来新会话也读到

---

## 项目演进总览

```
v0.0  ────  文档骨架（SECURITY/DEPLOY/ARCHITECTURE/ENGINES）
              ↓
v0.1  ────  LiveKit + token-server（最小骨架，浏览器加入房间）
              ↓
v0.2  ────  + agent-worker（FSM + VAD + mock 三件套）
              ↓
v0.3  ────  + stt-server（sherpa-onnx 中文 CPU 流式）
              ↓
v0.4  ────  + llm-server（ollama + Qwen2.5-1.5B OpenAI 兼容）
              ↓
v0.5  ────  + tts-server（Kokoro CPU）｜dev 全链路真引擎完工
              ↓
v0.5.1 ───  LLM 流式句切片 → 并发 TTS pipeline（首包延迟优化）
              ↓
v0.5+  ───  prod profile：vLLM + sherpa GPU + CosyVoice 2 GPU  [TODO]
```

## 经验教训摘录

| 教训 | 出处 |
|---|---|
| 选音频/AI Python 包前看依赖图，警惕 torch+CUDA 拖大 | v0.2 silero-vad |
| 模型托管 GitHub releases → HuggingFace 是趋势 | v0.3 |
| 模型仓库可能改名 / 鉴权变化（rubric-driven 选型抗风险） | v0.3 Paraformer→Zipformer |
| sherpa-onnx 1.13 API 漂移（`get_result` 类型变化） | v0.3 |
| `.env` 已存在 → `.env.example` 改了不会同步 | v0.4 |
| ollama 镜像带完整 CUDA（dev 浪费 / prod 零阻力） | v0.4 |
| OpenAI 兼容 API 是 LLM 切换的"通用快速接口" | v0.4 |
| TTS 真实性能极度依赖 CPU AVX2 支持（沙盒 vs 现代机差 10×） | v0.5 |
| CPU bound 时多并发 TTS 不带来收益（流式只省"早发"） | v0.5.1 |
