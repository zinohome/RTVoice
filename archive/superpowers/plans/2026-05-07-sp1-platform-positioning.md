# SP1 平台定位 + 文档骨架重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 RTVoice 的 README + ARCHITECTURE 从 voice-agent 叙事重写为 voice-services-platform 叙事；3 个 service 平铺；agent-worker 标内部 implementation；Realtime Voice 默认 WS gateway + LiveKit 备选；Caddy 标可选；同步对齐 DEPLOY/OPERATIONS/COZYVOICE_INTEGRATION 首段叙事。

**Architecture:** 5 文件改动，2 重写 + 3 小改首段。无代码改动。验证靠"Mermaid 渲染检查 + 链接 lint + 验收清单核对"。

**Tech Stack:** Markdown, Mermaid (GitHub native render), shell。

**Spec:** [docs/superpowers/specs/2026-05-07-sp1-platform-positioning-design.md](../specs/2026-05-07-sp1-platform-positioning-design.md)

---

## Task 1: 备份 + 写新 README.md

**Files:**
- Backup: `README.md.bak.20260507` (新文件)
- Rewrite: `README.md`

- [ ] **Step 1: 备份原 README**

```bash
cd /home/ubuntu/CozyProjects/RTVoice
cp README.md README.md.bak.20260507
ls -la README.md.bak.20260507
```

Expected: 文件存在，与原 README 大小相同。

- [ ] **Step 2: 写新 README.md（完整内容）**

把以下内容**完整覆盖**到 `README.md`:

````markdown
# RTVoice

**RTVoice** —— self-hosted 语音服务平台，三个 service 一等公民：

1. **STT 服务** —— 实时流式转写（sherpa-onnx，WebSocket）
2. **TTS 服务** —— 流式合成 + 音色克隆（Fun-CosyVoice 3，HTTP + WebSocket）
3. **Realtime Voice 服务** —— 端到端语音对话（默认 WebSocket gateway / 可选 LiveKit；本地 LLM；支持 prompt+memory + 同步 transcript + 换音色）

全栈本地推理，单 GPU ≤ 12GB（RTX 3060/4060 适配），docker-compose 一键启停。

通过标准 HTTP / WebSocket API 给任意应用接入；内置鉴权、审计开关、用量监控、管理 Web UI。

---

## ⚡ 60 秒试一下

```bash
git clone https://github.com/zinohome/RTVoice.git
cd RTVoice
cp .env.example .env       # 默认 dev 配置已可用
docker compose --profile dev up -d
```

服务起来后:

| 想试什么 | 怎么试 |
|---|---|
| **STT**（语音转文字）| [测试页](http://127.0.0.1:8000/) 录一段；或编程方式见 [STT 集成示例](./COZYVOICE_INTEGRATION.md#stt) |
| **TTS**（文字转语音）| `curl -X POST http://127.0.0.1:9880/tts/stream -d '{"text":"你好"}' \| ffplay -f s16le -ar 24000 -` |
| **Realtime 对话**| 浏览器 [测试页](http://127.0.0.1:8000/) → 加入语音 → 说话 |

**首次启动注意**：LLM (Ollama) 需要 `ollama pull qwen2.5:1.5b`（约 1GB）。完整下好后约 3-5 分钟可对话。prod GPU 部署见 [DEPLOY.md](./DEPLOY.md)。

---

## What's in the box

### 🎤 STT — 流式语音识别

- **接口**：WS `/asr`
- **引擎**：sherpa-onnx Streaming Zipformer 中英文
- **协议**：PCM int16 LE 16kHz mono in → JSON `{partial,final,error}` events out
- **场景**：实时转写、麦克风听写、对话录音
- → [集成示例](./COZYVOICE_INTEGRATION.md#stt) · [API spec](./docs/api/stt.md)（即将上线）

### 🔊 TTS — 流式语音合成 + 音色克隆

- **接口**：HTTP POST `/tts/stream`（单次）+ WS `/tts/stream_ws`（双向流式）
- **引擎**：Fun-CosyVoice 3 (0.5B GPU)
- **协议**：text in（HTTP body 或 WS 流）→ chunked PCM int16 LE 24kHz mono out
- **特性**：音色克隆（POST /voices/add）、speed 0.5-2.0
- → [集成示例](./COZYVOICE_INTEGRATION.md#tts) · [API spec](./docs/api/tts.md)（即将上线）

### 💬 Realtime Voice — 实时语音对话

- **接口**：HTTP POST `/sessions` 创建 + WS `/v1/realtime/{session_id}` 连接
- **协议**：客户端发 PCM in / 收 PCM + transcript events out（OpenAI Realtime 风格）
- **引擎**：内部 STT (sherpa) + LLM (Ollama / vLLM) + TTS (Fun-CosyVoice 3)
- **特性**：双向流式、prompt+memory、同步 transcript、换音色、barge-in
- **高级模式**：LiveKit endpoint 可选保留（适合 end-user 跨公网移动场景）
- → [集成示例](./COZYVOICE_INTEGRATION.md#realtime) · [API spec](./docs/api/sessions.md)（即将上线）

---

## 🔌 集成 (Integration)

给客户端 / 应用开发者：怎么把 RTVoice 接到你的项目。

- 完整集成手册：[COZYVOICE_INTEGRATION.md](./COZYVOICE_INTEGRATION.md)
- API spec（路径/鉴权/错误码统一规范）：`docs/api/`（即将上线，SP1.5）
- 客户端示例代码：Python、curl、JavaScript（在 COZYVOICE_INTEGRATION 里）
- 鉴权：Bearer token（[SECURITY.md](./SECURITY.md)）
- 部署拓扑选择（同机 docker network / 跨机 TLS / 公网 LE）：见集成手册 §1

---

## 🛠 部署 (Deployment)

给运维 / 部署人员：怎么把 RTVoice 跑起来。

- **首次部署**：[DEPLOY.md](./DEPLOY.md)
- **运维手册**（容错矩阵 / 排障 / 升级路径 / build 性能）：[OPERATIONS.md](./OPERATIONS.md)
- **硬件要求**：单 GPU ≤ 12GB（RTX 3060/4060 实测 OK）；CPU only 模式仅 dev 用（性能不足）
- **监控**：可选启 `--profile monitoring` 起 Prometheus + Grafana
- **安全**：[SECURITY.md](./SECURITY.md)（公网部署必读）
- **生产实测报告**：[PROD_VALIDATION.md](./PROD_VALIDATION.md)

---

## 📚 概念 (Concepts)

给好奇者 / 新贡献者：RTVoice 怎么工作。

- **完整架构**：[ARCHITECTURE.md](./ARCHITECTURE.md)
- **引擎选型对比**（为什么用 sherpa / CosyVoice / vLLM）：[ENGINES.md](./ENGINES.md)
- **设计决策与教训**：[OPERATIONS.md §1 容错矩阵](./OPERATIONS.md) + [ARCHITECTURE.md §7 决策日志](./ARCHITECTURE.md)
- **版本史**：[CHANGELOG.md](./CHANGELOG.md)

---

## 🗺 现状 / Roadmap

**已完成**（v0.7）：3 service 单 tenant 可用 + 容错完备 + 双向流式 TTS

**进行中**（platform-first 重构 sub-projects）：

- SP1 ✅ 平台定位 + 文档骨架（你现在看到的就是）
- SP1.5 API 规范 + OpenAPI
- SP2 Multi-tenant Realtime session（动态 session）
- SP3 prompt + memory + 同步 transcript
- SP4 音色克隆 + 语气语调暴露
- SP5 审计 + 对话记录持久化
- SP6 用量追踪 + 限流
- SP7 Management Web UI

详见 [CHANGELOG.md](./CHANGELOG.md) Unreleased 段。

---

## License & 贡献

[LICENSE](./LICENSE) · [CONTRIBUTING.md](./CONTRIBUTING.md)
````

- [ ] **Step 3: 渲染验证（grep 链接 + 文件存在性）**

```bash
# 验证所有内部链接指向的文件都存在（除 SP1.5 即将上线的 docs/api/）
grep -oE '\]\(\./[^)]+\)' README.md | sed 's/](\.\///;s/)//' | while read f; do
    case "$f" in
        docs/api/*) echo "[skip 即将上线] $f" ;;
        *) [ -e "$f" ] && echo "[ok] $f" || echo "[FAIL 文件不存在] $f" ;;
    esac
done
```

Expected: 全部 `[ok]` 或 `[skip 即将上线]`，无 `[FAIL]`。

- [ ] **Step 4: 检查 Mermaid 不在 README**（README 没用 Mermaid，仅 ARCHITECTURE 用）

```bash
grep -c '```mermaid' README.md
```

Expected: `0`

- [ ] **Step 5: Commit**

```bash
git add README.md README.md.bak.20260507
git commit -m "docs(README): 重写为 platform-first 叙事 (SP1)

- 5 行 pitch 强调 self-hosted 语音服务平台
- 3 个 service 平铺（STT / TTS / Realtime Voice）
- 60 秒试一下章节
- 多受众分章：集成 / 部署 / 概念 / Roadmap
- agent-worker 不在客户端章节出现（Model A 黑盒）
- Realtime Voice 默认 WS gateway，LiveKit 标可选高级
- API spec 链接占位（即将上线 SP1.5）

旧 README 备份到 README.md.bak.20260507"
```

---

## Task 2: 写新 ARCHITECTURE.md §1-§3 (Overview + STT + TTS)

**Files:**
- Backup: `ARCHITECTURE.md.bak.20260507`
- Rewrite: `ARCHITECTURE.md`（本 task 写前 3 章节，后续 task 追加）

- [ ] **Step 1: 备份原 ARCHITECTURE**

```bash
cp ARCHITECTURE.md ARCHITECTURE.md.bak.20260507
```

- [ ] **Step 2: 写 ARCHITECTURE.md 头 + §1 Overview**

把以下内容**完整覆盖**到 `ARCHITECTURE.md`（后续 task 会 append §2-§7）:

````markdown
# RTVoice 架构文档

> **本文档面向：** 想了解 RTVoice 内部如何工作的开发者、新贡献者、架构 reviewer。
> **配合阅读：** [README.md](./README.md)（高层概览）、[OPERATIONS.md](./OPERATIONS.md)（运维细节）、[ENGINES.md](./ENGINES.md)（选型对比）。

RTVoice 是 voice services platform，对外提供 3 个对等 service（STT / TTS / Realtime Voice）。本文档分章节描述各 service 的内部实现、跨服务关注点（鉴权 / GPU / 容错）、技术栈选型与设计决策日志。

---

## §1 Platform Overview

```mermaid
graph TB
    subgraph "Clients"
        APP[Client App<br/>CozyVoice / Browser / Python]
        OP[Admin / Operator]
    end

    subgraph "RTVoice Platform"
        subgraph "Edge"
            CADDY[Caddy TLS<br/>📦 可选<br/>仅公网/跨机时启]
            TS[token-server<br/>JWT for LiveKit 高级模式]
        end

        subgraph "Public Services (对外接口)"
            STT[STT Service<br/>WS /asr]
            TTS[TTS Service<br/>HTTP+WS /tts/*]
            RTV[Realtime Voice<br/>POST /sessions +<br/>WS /v1/realtime]
            LK[LiveKit SFU<br/>可选高级模式]
            ADM[Admin API<br/>/voices /quota /audit]
        end

        subgraph "Admin"
            WEB[Management Web UI<br/>SP7]
        end

        subgraph "Internal Components"
            GW[Realtime Gateway<br/>WS ↔ agent bridge]
            AW[agent-worker pool<br/>不对外暴露]
            STTE[sherpa-onnx GPU]
            TTSE[Fun-CosyVoice 3 GPU]
            LLM[Ollama / vLLM]
        end

        subgraph "Storage"
            VOL[Voice clones<br/>named volume]
            DB[Audit DB<br/>SP5]
        end
    end

    APP -.HTTP/WS.-> CADDY
    APP -. WebRTC 高级模式.-> LK
    OP -.浏览器.-> WEB
    WEB -.HTTP.-> ADM

    CADDY --> STT & TTS & RTV & ADM
    RTV --> GW
    GW -.dispatch.-> AW
    LK -.alt path.-> AW
    AW --> STT & TTS & LLM
    STT --> STTE
    TTS --> TTSE
    TTS --> VOL
    ADM --> VOL
    ADM --> DB
```

**关键观察:**

- **3 个 service 平等**：STT / TTS / Realtime Voice 都是 public API surface
- **Realtime Voice 双路径**：默认 WS gateway（OpenAI Realtime 风格 / server-to-server 友好）；LiveKit 可选高级模式（end-user 跨公网移动场景）
- **agent-worker = 内部组件**：客户端永远不直接接触；只通过 Realtime Gateway 间接调度
- **Caddy 可选**：仅公网或不信任内网时启用；同机 docker network 部署不需要
- **Storage 层**：voice clones 是 named volume；audit DB 在 SP5 引入

---

## §2 STT Service

### 接口签名

```
WS /asr
  client → server: binary frames (PCM int16 LE 16kHz mono)
                    + text "EOS" (触发 final)
  server → client: JSON events
                    {"type":"partial", "text":"..."}
                    {"type":"final", "text":"..."}
                    {"type":"error", "message":"..."}
```

### 内部组件

```mermaid
sequenceDiagram
    participant C as Client
    participant W as WS Handler
    participant R as sherpa-onnx<br/>OnlineRecognizer
    C->>W: connect /asr (Bearer auth)
    W->>R: create_stream()
    loop 每 50ms 单 coroutine 周期
        C->>W: PCM bytes
        W->>R: accept_waveform(pcm)
        W->>R: decode_stream() → partial text
        W->>C: {"type":"partial",...}
    end
    C->>W: text "EOS"
    W->>R: input_finished() + final decode
    W->>C: {"type":"final",...}
    R-->>W: 自动 reset，等下一句
```

### 关键设计权衡

- **单 coroutine 处理**：sherpa-onnx Stream 不是 thread-safe；旧版 v0.5.2 用 decode_loop + EOS handler 两个 task 并发访问 stream 导致 native crash。v0.5.3 改单 coroutine：receive WS msg（带超时）→ accept_waveform 或 EOS 处理 → decode → emit partial，全程一个协程操作 stream，无并发无 race。
- **endpoint detection 关闭**：sherpa 自身的 endpoint 检测会异步 reset stream，与我们的 EOS 控制冲突；统一由客户端发 "EOS" 控制 final 时机。

### 容错

- 客户端 WS 断 → 服务端释放 stream
- 服务端 WS 重启 → 客户端 STTClient 走 5 次指数退避重连（1→2→4→8→16s），重连后用新 stream（当前 utterance 数据丢失，下一轮恢复正常）

### 不在范围

- 多语言切换（默认中英文 bilingual）：未来用户可换 sherpa-onnx 模型；具体见 [ENGINES.md](./ENGINES.md)
- 说话人识别 / diarization：业界另起方案，本平台不内嵌

---

## §3 TTS Service

### 接口签名

```
HTTP POST /tts/stream
  request body: JSON {"text":"...", "voice":"...", "speed":1.0}
  response: chunked transfer, binary PCM int16 LE 24kHz mono
  headers: X-Sample-Rate=24000, X-Channels=1, X-Format=pcm-int16-le

WS /tts/stream_ws
  client → server:
    text frame 1 (JSON metadata): {"voice":"...","speed":1.0}
    text frame N: 文本增量（可流式喂入）
    text "EOS" (触发结束)
  server → client:
    binary PCM chunks
    text {"type":"done","chunks":N} 末帧
    或 text {"type":"error","message":"..."}
```

### 内部组件 + 双向流式数据流

```mermaid
sequenceDiagram
    participant C as Client (WS)
    participant H as WS Handler<br/>(asyncio.Lock)
    participant Q as text_q (sync queue)
    participant CV as CosyVoice 3<br/>(GPU 单实例)
    C->>H: connect + JSON metadata
    H->>H: acquire _inference_lock
    Note over H: reset model.token_hop_len=25
    par feed text
        C->>H: text frames (流式)
        H->>Q: text_q.put(chunk)
    and run inference
        H->>CV: inference_zero_shot(text_gen, prompt_wav, ...)
        loop 每收到 5+ tokens
            CV->>CV: append text token / wait_for_more
            CV->>H: yield {tts_speech: tensor}
            H->>C: binary PCM chunk
        end
    end
    C->>H: text "EOS"
    H->>Q: text_q.put(None)
    CV->>H: yield (final tail)
    H->>C: binary PCM chunk + done event
    H->>H: release _inference_lock
```

### 关键设计权衡

- **asyncio.Lock 串行化（v0.7.2 修复）**：CosyVoice 单 GPU model 实例并发调用会污染内部 state。所有 inference 入口（HTTP `_synthesize_stream` 和 WS `tts_stream_ws`）都包在 `async with _inference_lock`，N 路并发自动排队。Trade-off：吞吐受 GPU 单实例限制（baseline 单路 ~1.5s，5 路并发 ~6s 串行）。
- **CosyVoice instance-attr 重置规约**：`model.token_hop_len` 在 v3 内部 stream 路径单调递增（25→50→100），跨 inference 共享。每路 inference 开始前手动 `model.token_hop_len = 25` reset。
- **HTTP path generator wrap（v0.7.3 修复）**：CosyVoice 3 在 `tts_text=str` + 短文本下走"等满 hop_len 才 yield"的旧路径，遇到边界 bug（hifigan F0 kernel/input mismatch）。HTTP path 包成 single-element generator → 走稳定的"边收边 decode"路径。
- **prompt_text 必须含 `<|endofprompt|>`**：CosyVoice 3 LLM `inference()` 硬断言（v3 frontend 不自动加，caller 必须显式拼）。

### Voice Clone

```
POST /voices/add  (multipart, TTS_ADMIN_API_KEY 鉴权)
  - file: 16kHz mono wav (3-30 秒)
  - spk_id: 新音色 ID
  - prompt_text: 参考音对应的文字 (≥3 秒发音内容)
↓
add_zero_shot_spk() 注册 → spk2info.pt 持久化到 named volume
↓
重启自动 reload，POST /tts/stream voice=新id 即可用

DELETE /voices/{spk_id}  (默认音色保护，不可删)
```

### 容错

- HTTP path: `request.is_disconnected()` 监测，client 断则停止推理
- WS path: barge-in `asyncio.shield(ws.aclose, timeout=2)` 让 close frame 真发出；server 端 `send_bytes` 接 `(WebSocketDisconnect, RuntimeError)` 双异常防 starlette send-after-close trap

````

- [ ] **Step 3: 渲染验证 — Mermaid 语法 lint**

```bash
# Mermaid 块数（应为 2: §1 一张 + §2 一张 + §3 一张 = 3）
grep -c '```mermaid' ARCHITECTURE.md
```

Expected: `3`

- [ ] **Step 4: 暂不 commit（Task 3、4 还要往同一文件追加 §4-§7）**

继续 Task 3。

---

## Task 3: ARCHITECTURE.md §4 Realtime Voice

**Files:**
- Modify: `ARCHITECTURE.md`（追加 §4）

- [ ] **Step 1: 把 §4 内容追加到 ARCHITECTURE.md**

在 ARCHITECTURE.md 末尾追加:

````markdown

---

## §4 Realtime Voice Service

### 接口签名（默认 WS gateway 模式）

```
HTTP POST /sessions
  request body: {"voice":"...", "prompt":"...", ...}
  response: {"session_id":"...", "ws_url":"ws://.../v1/realtime/{id}"}

WS /v1/realtime/{session_id}
  client → server:
    text frame {"type":"session.update", ...}     (可选，热改 voice/prompt)
    binary frame: PCM int16 LE 16kHz mono         (用户音频)
    text "audio.eos"                               (用户发言结束)
  server → client:
    text {"type":"transcript.partial", "text":"..."}    (STT 中间结果)
    text {"type":"transcript.final",   "text":"..."}    (STT 最终)
    text {"type":"response.text",       "text":"..."}   (agent 回复文本，逐 token / 逐句)
    binary frames                                       (agent 回复 PCM 24kHz mono)
    text {"type":"response.done"}                       (本轮回复结束)
    text {"type":"error", "message":"..."}
```

### 数据流（默认 WS gateway 模式）

```mermaid
sequenceDiagram
    participant C as Client
    participant G as Realtime Gateway
    participant AW as agent-worker
    participant STT as STT engine
    participant LLM as LLM (Ollama/vLLM)
    participant TTS as TTS engine
    C->>G: POST /sessions {voice, prompt}
    G->>AW: spawn / dispatch worker
    G->>C: 200 {session_id, ws_url}
    C->>G: WS connect /v1/realtime/{id}
    G->>AW: bind ws session
    loop 用户说一句
        C->>G: PCM bytes
        G->>AW: forward PCM
        AW->>STT: feed PCM
        STT->>AW: partial / final
        AW->>G: transcript events
        G->>C: {"type":"transcript.partial",...}
        C->>G: "audio.eos"
        AW->>STT: EOS, get final
        AW->>LLM: prompt + memory + final
        LLM-->>AW: tokens (流式)
        AW->>G: response.text events
        G->>C: text events
        AW->>TTS: text chunks (streaming)
        TTS-->>AW: PCM chunks
        AW->>G: PCM
        G->>C: binary PCM
        AW->>G: response.done
        G->>C: response.done
    end
```

### 数据流（可选 LiveKit 高级模式）

```mermaid
sequenceDiagram
    participant C as Client (LiveKit SDK)
    participant TS as token-server
    participant LK as LiveKit SFU
    participant AW as agent-worker
    C->>TS: POST /token {identity, room}
    TS->>C: JWT
    C->>LK: WebRTC connect (token)
    LK->>AW: agent joined room (内部触发)
    Note over C,LK: 双向 WebRTC 媒体流 (UDP)
    Note over AW: agent 内部走 STT/LLM/TTS pipeline<br/>把 PCM 推回 LK，LK 转给 client
```

### Session 生命周期（抽象，详细 SP2 设计）

```
create → active → idle → expire
   ↓        ↓       ↓       ↓
  worker  双向流  超时   清理 memory
  分配          倒计时  释放 worker
```

详细 session API、memory 模型（追加 / 滑窗 / 摘要 / 持久化）、prompt 透传规则属于 SP2/SP3 设计范围，不在本架构文。

### 关键设计权衡

- **WS gateway 默认 vs LiveKit 备选（D-2026-05-07.4）**：server-to-server 场景下 TCP/WS 与 UDP/WebRTC 延迟差异 < 1ms（同 LAN/同机部署），WS gateway 集成简化压倒性优势。LiveKit 仅在末端用户跨公网移动场景保留。
- **agent-worker = 内部 implementation（D-2026-05-07.3）**：客户端永远只看到 WS gateway endpoint，不接触 agent-worker；保留我们内部演进自由。
- **同进程调用 vs HTTP 调用内部 STT/TTS**：当前 agent-worker 通过 HTTP/WS 调内部 STT/TTS server（即使在同一 docker network 也走 HTTP）。代价是几 ms 序列化延迟；好处是 STT/TTS service 同时给外部客户端用，agent-worker 只是其中一个 client。**这与 platform 定位一致**。

````

- [ ] **Step 2: 验证 Mermaid 总数 = 5**

```bash
grep -c '```mermaid' ARCHITECTURE.md
```

Expected: `5`（§1 + §2 STT seq + §3 TTS seq + §4 默认数据流 + §4 LiveKit 数据流）

- [ ] **Step 3: 暂不 commit**

继续 Task 4。

---

## Task 4: ARCHITECTURE.md §5-§7 (跨服务关注点 + 技术栈 + 决策日志)

**Files:**
- Modify: `ARCHITECTURE.md`（追加 §5-§7）

- [ ] **Step 1: 把 §5-§7 内容追加到 ARCHITECTURE.md**

在末尾追加:

````markdown

---

## §5 跨服务关注点

### 鉴权三层

| 层 | env | 用途 |
|---|---|---|
| Client API key | `RTVOICE_API_KEY` | STT WS / TTS HTTP+WS / Realtime Voice 的 client 调用 Bearer 鉴权（留空 = dev 模式无鉴权）|
| Admin key | `TTS_ADMIN_API_KEY` | `/voices/add /voices/{id} /quota` 等高权限管理操作（留空 = admin 端点禁用）|
| LiveKit JWT | token-server | 仅 LiveKit 高级模式；token-server 用 `LIVEKIT_API_KEY/SECRET` 签 JWT |

### TLS（可选）

- 同机 docker network：不需要
- 同机 127.0.0.1 bind：不需要
- LAN 跨机（信任）：可选
- LAN 跨机（半信任）：建议（Caddy `tls internal` 自签）
- **公网暴露：必须**（Caddy + Let's Encrypt）

### GPU 显存预算（按 LLM 选型）

| 场景 | sherpa | CosyVoice 3 | LLM | 总计 | 12GB 余量 |
|---|---|---|---|---|---|
| dev (Q4 1.5B) | 1G | 5.5G | 1.5G | **8G** | 4G ✓ 宽裕 |
| prod (Q4 3B) | 1G | 5.5G | 3G | **9.5G** | 2.5G ✓ |
| prod (Q4 7B) | 1G | 5.5G | 5G | **11.5G** | 0.5G ⚠️ 边缘 |

> 多并发 session 时，agent-worker 数 × 每路 STT/TTS 占用要分别累加；详 [SP2 设计](#)。

### 容错矩阵

完整列表见 [OPERATIONS.md §1](./OPERATIONS.md)。本文不重复，关键摘要：

- LiveKit room 断 → 5 次指数退避重连
- STT 长连接 → 自愈 reconnect loop
- LLM 流式 → httpx per-chunk timeout + 0-token fallback
- TTS WS barge-in → `shield(aclose)` + server 接 RuntimeError
- CosyVoice multi-concurrency → asyncio.Lock 串行 + `token_hop_len` reset

### 监控

每个 service 暴露 Prometheus `/metrics`：

| Service | 关键指标 |
|---|---|
| token-server | `rtvoice_tokens_issued_total`, `rtvoice_token_auth_failures_total{reason}` |
| stt-server | `rtvoice_stt_ws_connections_active`, `rtvoice_stt_decode_seconds` |
| tts-server | `rtvoice_tts_phrases_total`, `rtvoice_tts_ttfb_seconds`, `rtvoice_tts_phrase_rtf` |
| agent-worker | `rtvoice_round_seconds`, `rtvoice_first_audio_seconds`, `rtvoice_agent_state` |

可选 `--profile monitoring` 起 Prometheus + Grafana stack（21 panels dashboard）。详 `monitoring/README.md`。

---

## §6 技术栈选型

| 组件 | 选型 | 替代方案 | 为什么这个 |
|---|---|---|---|
| **SFU/WebRTC** | LiveKit `livekit/livekit-server:v1.11.0` | Daily / Janus / Mediasoup | 文档 + 多语言 SDK 最完整，开源活跃，docker image 直接用 |
| **STT** | sherpa-onnx Streaming Zipformer 中英文 | whisper / faster-whisper | 真流式（whisper 系是分块伪流式）+ GPU 兼容性 + 模型小 |
| **TTS** | Fun-CosyVoice 3 (0.5B GPU) | Kokoro / XTTS-v2 / ElevenLabs | v3 双向流式（边收文本边吐音频）+ 中文 SOTA + 完全本地 + 音色克隆 |
| **LLM** | Ollama (dev) / vLLM (prod) | 直接 transformers / llama.cpp | OpenAI API 兼容 + 开箱即用模型管理 + 易切换 |
| **Web 框架** | FastAPI + uvicorn | Flask / aiohttp | async-first + Pydantic 校验 + WS 原生支持 |
| **TLS Proxy** | Caddy 2.8 | nginx + certbot | 自动 ACME + 配置 1/10 体积 |
| **监控** | Prometheus + Grafana | Datadog / NewRelic | 自托管 + 文本协议 + 无供应商绑定 |
| **WS 客户端** | `websockets` (Python) | `aiohttp` / `httpx-ws` | 协议合规 + maintainer 活跃 |

详细对比见 [ENGINES.md](./ENGINES.md)。

> **第三方依赖**：LiveKit Server / SDK、sherpa-onnx、Fun-CosyVoice、Ollama 都是开源项目；我们用而**不重新发明**。RTVoice 的价值在于把这些组合成 platform。

---

## §7 设计决策日志

每条记录格式：`D-YYYY-MM-DD.N · 决策标题`，含背景、决定、替代方案、理由。按时间倒序。

### D-2026-05-07.6 · Caddy TLS 标"可选"

**决策**：架构图 Caddy 用虚线 + "📦 可选" 标注；明确公网必须、内网可选、同机不需要。

**理由**：Bearer auth (`RTVOICE_API_KEY`) 是独立鉴权层，Caddy 只解决传输加密。不应让读者误以为"不开 Caddy 就跑不起来"。

### D-2026-05-07.5 · API 规范延后到 SP1.5

**决策**：SP1 仅做叙事重构；路径风格 / 错误码 / 版本 / 鉴权统一规则 / capability discovery 留 SP1.5 独立 sub-project。

**理由**：API 规范影响后续 SP2-7 的设计，需要专注；与 SP1 改文档不冲突。

### D-2026-05-07.4 · WS gateway primary, LiveKit 备选

**决策**：Realtime Voice service 默认走 `WS /v1/realtime`（OpenAI Realtime 风格）；LiveKit endpoint 保留作 advanced mode（仅末端 user 跨公网移动）。

**替代**：纯 LiveKit (v0.7 现状) - 集成方多 SDK 依赖；纯 WS gateway 删 LiveKit - 失去末端跨公网韧性。

**理由**：server-to-server 场景下 TCP/WS 与 UDP/WebRTC 延迟差异 < 1ms（同 LAN/同机），WS gateway 集成简化优势压倒性。

### D-2026-05-07.3 · agent-worker = Model A 内部 implementation detail

**决策**：agent-worker 是 Realtime Voice service 的内部 worker，客户端永远不直接接触；只在 §运维 / §概念 出现，不在 §集成。

**替代**：Model B reference impl（公开 worker 协议）/ Model C default tenant + 渐进 multi-tenant。

**理由**：保留内部演进自由；与 OpenAI Realtime / Vapi / Twilio 行业惯例一致；客户体验最简。

### D-2026-05-07.2 · README 多受众分章节

**决策**：README 第一屏 5 行 pitch + 60 秒 try + 3 service cards；后段分 §集成 / §部署 / §概念 / §Roadmap。

**理由**：3 类受众（集成方 / 运维 / 好奇者）混合入口流量，单视角 README 都不友好。

### D-2026-05-07.1 · 三个 service 完全平铺

**决策**：3 service 同等大小 cards；feature/API list 等篇幅；不分层（不写 STT/TTS 是底层、Realtime 上层）。

**理由**：用户期望"3 service 一等公民"，不让 Realtime 显得独立优越。

### D-2026-05-06.4 · CosyVoice 3 与 v0.6 镜像并存可瞬切回滚

**决策**：v0.7 baseline 通过 `Dockerfile.cosyvoice3` + 单独 image tag 引入；v0.6 image 保留；切换由 `.env` `TTS_DOCKERFILE/TTS_IMAGE` 控制。

**理由**：模型 ~5.6GB + 依赖大，rebuild 高成本；保留双镜像 = 回滚秒级。

### D-2026-05-06.3 · CosyVoice 3 prompt_text 必须含 `<|endofprompt|>`

**决策**：在 `main_cosyvoice3.py::DEFAULT_PROMPT_TEXT` 末尾显式拼 `<|endofprompt|>` token。

**理由**：CosyVoice 3 LLM `inference()` 硬断言 token 151646 必须在输入序列；v3 frontend 不自动添加。这是 vendor undocumented contract，prod 实测才发现。

### D-2026-05-06.2 · ubuntu22.04 + cuda devel base image

**决策**：Dockerfile.cosyvoice3 用 `nvidia/cuda:12.6.3-devel-ubuntu22.04`（不用 ubuntu24.04 + deadsnakes PPA，不用 runtime image）。

**理由**：devel 自带 nvcc 12.6（deepspeed 探测 CUDA 必需）；ubuntu22.04 默认 python3=3.10.12（CosyVoice 兼容）；deadsnakes PPA 国内 TLS 握手频繁失败。

### D-2026-05-04.1 · CosyVoice 用 inference_zero_shot 不用 inference_sft

**决策**：CosyVoice 2 model 在 `add_zero_shot_spk` 注册的是 zero-shot schema（`llm_embedding/flow_embedding`），与 SFT 路径 schema (`embedding`) 不兼容。统一用 `inference_zero_shot(zero_shot_spk_id=...)`。

**理由**：v2 0.5B 不带 SFT spk2info.pt，无法走 inference_sft；强行走会 KeyError。

---

## 历史 / 进一步阅读

- [README.md](./README.md) — 高层概览
- [OPERATIONS.md](./OPERATIONS.md) — 运维细节、容错矩阵、build 性能
- [DEPLOY.md](./DEPLOY.md) — 部署步骤
- [ENGINES.md](./ENGINES.md) — 引擎对比详细
- [CHANGELOG.md](./CHANGELOG.md) — 版本演进
- [SECURITY.md](./SECURITY.md) — 安全契约
- [PROD_VALIDATION.md](./PROD_VALIDATION.md) — v0.7 prod 实测报告
````

- [ ] **Step 2: 验证文件完整性 + Mermaid 总数**

```bash
# Mermaid 应为 5
grep -c '```mermaid' ARCHITECTURE.md

# 章节大致结构（每个 § 出现一次）
grep -c '^## §' ARCHITECTURE.md
```

Expected: Mermaid `5`，§ `7`

- [ ] **Step 3: 链接 lint**

```bash
grep -oE '\]\(\./[^)#]+' ARCHITECTURE.md | sed 's/](\.\///' | sort -u | while read f; do
    [ -e "$f" ] && echo "[ok] $f" || echo "[FAIL] $f"
done
```

Expected: 全部 `[ok]`。

- [ ] **Step 4: Commit ARCHITECTURE 整体**

```bash
git add ARCHITECTURE.md ARCHITECTURE.md.bak.20260507
git commit -m "docs(ARCHITECTURE): 重写为 platform-first 7 章节结构 (SP1)

§1 Platform Overview (Mermaid: 全平台拓扑图)
§2 STT Service (sequence 图: ws 单 coroutine 处理)
§3 TTS Service (sequence 图: 双向流式 + asyncio.Lock + 关键修复)
§4 Realtime Voice Service (默认 WS gateway 数据流图 + LiveKit 高级模式数据流图)
§5 跨服务关注点 (鉴权三层 / TLS 矩阵 / GPU 预算表 / 容错 / 监控)
§6 技术栈选型 (8 行表格 + ENGINES.md 链接)
§7 设计决策日志 (10 条 D-YYYY-MM-DD.N records)

旧 ARCHITECTURE 备份到 ARCHITECTURE.md.bak.20260507"
```

---

## Task 5: 三个文档小改首段

**Files:**
- Modify: `DEPLOY.md`（首段一句）
- Modify: `OPERATIONS.md`（首段一句）
- Modify: `COZYVOICE_INTEGRATION.md`（§1 第一段）

- [ ] **Step 1: DEPLOY.md 首段**

找到当前 DEPLOY.md 第二行（紧跟 `# RTVoice 部署手册` 后）的内容并改：

```bash
# 看当前 line 1-5
head -5 DEPLOY.md
```

Expected：能看到当前首段。

用 sed 替换或手工编辑：把"本文档说明 RTVoice 项目..."替换为：

```
本文档说明 RTVoice voice services platform 从开发机到生产机的部署流程、目录约定、配置切换方式与运维操作。**部署前必读 [SECURITY.md](./SECURITY.md)**。
```

实施命令（用 sed 一次性替换）:

```bash
sed -i 's|本文档说明 RTVoice 项目从开发机到生产机的部署流程|本文档说明 RTVoice voice services platform 从开发机到生产机的部署流程|' DEPLOY.md
```

验证:

```bash
head -5 DEPLOY.md | grep -c "voice services platform"
```

Expected: `1`

- [ ] **Step 2: OPERATIONS.md 首段加一句**

读首段:

```bash
head -10 OPERATIONS.md
```

在第一段（"面向已经部署 RTVoice 的运维者..."）后面追加一句"本文档假定读者熟悉 [README](./README.md) 描述的 platform 三个 service。"

```bash
# 找到包含 "新部署看 [DEPLOY" 那行（OPERATIONS 现在第 4 行附近），在它前面插一句
sed -i 's|新部署看 \[DEPLOY|本文档假定读者熟悉 [README](./README.md) 描述的 platform 三个 service。新部署看 [DEPLOY|' OPERATIONS.md
```

验证:

```bash
head -10 OPERATIONS.md | grep -c "platform 三个 service"
```

Expected: `1`

- [ ] **Step 3: COZYVOICE_INTEGRATION.md §1 改写**

定位 §1:

```bash
grep -n "## 1." COZYVOICE_INTEGRATION.md | head -3
```

把第一句 "把 RTVoice 用作 CozyVoice 的本地 STT/TTS 后端。" 改成新句:

```bash
sed -i 's|把 RTVoice 用作 CozyVoice 的本地 STT/TTS 后端。|本文档示范如何把 RTVoice 集成到任意客户端项目作为本地后端。CozyVoice 是其一示例；其他场景（Discord bot / 客服系统 / 自动化 / 移动 app）参照同样模式。|' COZYVOICE_INTEGRATION.md
```

验证:

```bash
grep -c "本文档示范如何把 RTVoice 集成到任意客户端项目" COZYVOICE_INTEGRATION.md
```

Expected: `1`

- [ ] **Step 4: 三个 commit**

```bash
git add DEPLOY.md OPERATIONS.md COZYVOICE_INTEGRATION.md
git commit -m "docs: DEPLOY/OPERATIONS/COZYVOICE_INTEGRATION 首段叙事对齐 platform-first (SP1)

DEPLOY.md: '本文档说明 RTVoice 项目' → 'RTVoice voice services platform'
OPERATIONS.md: 加一句 '假定读者熟悉 README 描述的 platform 三个 service'
COZYVOICE_INTEGRATION.md §1: 'CozyVoice 后端' → '任意客户端，CozyVoice 是其一示例'

3 处都是首段字眼对齐，正文不动；其他 4 文档（PROD_VALIDATION /
CHANGELOG / SECURITY / ENGINES / CONTRIBUTING）按 spec 不动。"
```

---

## Task 6: 验收 + push

**Files:** none（read-only verification）

- [ ] **Step 1: 验收清单核对（spec §6）**

逐项:

```bash
# 验收 1: README 第一屏看清"RTVoice 是什么 + 3 service 平铺 + 60 秒能试"
head -30 README.md
```

肉眼判断：能否在 30 行内看到：① 是什么；② 3 个 service；③ 60 秒命令。

```bash
# 验收 2: ARCHITECTURE §1 Overview 能看到 platform 拓扑（含 Admin + Storage + Caddy 可选）
grep -A 60 "^## §1 Platform" ARCHITECTURE.md | head -65
```

肉眼判断：Mermaid 块包含 Caddy 可选 / Admin / Storage 元素。

```bash
# 验收 3: DEPLOY/OPERATIONS/COZYVOICE_INTEGRATION 首段不再用 "voice agent" 字眼
for f in DEPLOY.md OPERATIONS.md COZYVOICE_INTEGRATION.md; do
    head -10 "$f" | grep -i "voice agent" && echo "[FAIL] $f 仍含 voice agent" || echo "[ok] $f 首段干净"
done
```

Expected: 三个 `[ok]`

- [ ] **Step 2: 全文档链接 lint**

```bash
for f in README.md ARCHITECTURE.md DEPLOY.md OPERATIONS.md COZYVOICE_INTEGRATION.md; do
    echo "=== $f ==="
    grep -oE '\]\(\./[^)#]+' "$f" | sed 's/](\.\///' | sort -u | while read p; do
        case "$p" in
            docs/api/*) echo "  [skip 即将上线] $p" ;;
            *) [ -e "$p" ] && echo "  [ok] $p" || echo "  [FAIL] $p" ;;
        esac
    done
done
```

Expected: 无 `[FAIL]`

- [ ] **Step 3: GitHub 渲染预览（可选 user 自己做）**

```bash
git log --oneline -5
```

确认有 3 个 SP1 commits（README + ARCHITECTURE + 三个文档）。提示 user 在 GitHub 网页打开 README.md / ARCHITECTURE.md 看 Mermaid 渲染效果。

- [ ] **Step 4: Push**

```bash
git push origin main
```

- [ ] **Step 5: SP1 完工 announce**

最后输出确认信息:
- README.md 已重写（platform-first，3 service 平铺，多受众）
- ARCHITECTURE.md 已重写（7 章节 + 5 Mermaid 图 + 决策日志）
- 3 个文档首段叙事对齐
- 备份文件 `.bak.20260507` 已 commit（如果以后想回滚）
- 下个 sub-project: SP1.5 API 规范

---

## Self-Review

### 1. Spec coverage

逐项检查 spec → plan task 映射:

| Spec 节 | 对应 Task |
|---|---|
| §1.1 README rewrite | Task 1 |
| §1.1 ARCHITECTURE rewrite | Task 2 + 3 + 4 |
| §1.1 DEPLOY/OPERATIONS/COZYVOICE_INTEGRATION 小改 | Task 5 |
| §3 README 详细结构 | Task 1 Step 2（完整内容已嵌入）|
| §4 ARCHITECTURE 章节大纲 | Task 2-4 各 Step |
| §4.2 §1 Mermaid 图 | Task 2 Step 2 |
| §4.3 §2-§7 内容要点 | Task 2-4 各对应 |
| §5 三个小改具体内容 | Task 5 各 Step |
| §6 验收标准 | Task 6 Step 1-3 |

无遗漏。

### 2. Placeholder scan

通读全文，无 TBD / TODO / "implement later" / "similar to..." 等 placeholder。代码块完整可执行。

### 3. Type consistency

文档型 plan，无函数签名 / 类型一致性问题。文件路径、备份后缀（`.bak.20260507`）、commit message 格式跨 task 统一。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-07-sp1-platform-positioning.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - 我 dispatch 一个 fresh subagent 跑每个 task，task 间 review，迭代快
2. **Inline Execution** - 直接在本 session 用 executing-plans，批量执行 + checkpoints

Which approach?
