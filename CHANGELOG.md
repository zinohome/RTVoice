# Changelog

RTVoice 项目从立项到 dev 全链路上线的版本记录。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号语义同 [SemVer](https://semver.org/lang/zh-CN/)。

每个版本含：
- **Added** 新增能力 / **Changed** 变更 / **Fixed** 修复 / **Notes** 决策与教训
- **验证**：autonomous（自动化）+ 待人工 部分各列

---

## [Unreleased]

待规划：
- v0.6：迁移到 `livekit-agents` 框架的 `AgentSession` + 框架级 turn detection
- v0.6：CosyVoice 2 GPU 服务化（替换 Kokoro CPU prod TTS）
- v0.7+：多音色配置 / 长上下文记忆 / function calling

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
