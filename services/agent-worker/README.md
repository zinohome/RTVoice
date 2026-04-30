# agent-worker

**职责**：加入 LiveKit 房间，订阅用户音频，跑 VAD + 状态机，驱动 STT → LLM → TTS 流水线。

**状态**：✅ v0.5.1（STT/LLM/TTS 全切独立服务 + LLM 流式句切片 → TTS pipeline）

## 技术栈

| 组件 | 选型 | 备注 |
|---|---|---|
| LiveKit 客户端 | `livekit==1.0.16` (rtc) + `livekit-api==1.1.0` | 低层 rtc.Room API |
| VAD | `onnxruntime` + 手动下载 silero VAD ONNX (~2MB) | **不用 silero-vad pip 包**（拖 torch 12GB） |
| STT | WS 客户端 → stt-server (sherpa-onnx Streaming Zipformer) | 长连接，PCM 流式 + EOS |
| LLM | OpenAI SDK → llm-server (ollama + Qwen2.5-1.5B) | `/v1/chat/completions` 流式 SSE |
| TTS | mock 内嵌（in-process） | v0.5 真引擎 |
| WebSocket | `websockets==13.1` | STT 客户端依赖 |
| OpenAI SDK | `openai==1.59.7` | LLM 客户端依赖 |

**镜像大小**：~535MB（vs silero-vad pip 版 12GB+）

## 架构（v0.2）

低层 `rtc.Room` API + 自写状态机，**不用 livekit-agents 框架**。理由见 [ARCHITECTURE.md ADR](../../ARCHITECTURE.md#12-决策记录adr-摘要)：v0.2 mock 阶段没有真引擎可填进 AgentSession plugins，框架就是空壳；v0.4+ 真 STT/TTS/LLM 到位时再迁移。

```
LiveKit room
   ↓ subscribe user audio
agent.py
   ↓ AudioStream(sample_rate=16000) → 512-sample frame
vad.py (silero ONNX)
   ↓ speech_start / speech_end
state_machine.py
   ↓ Idle → Listening → Thinking → Speaking → (Interrupted) → Idle
mock_pipeline.py
   ├─ mock_stt() → 假转写
   ├─ mock_llm() → 流式假 token
   └─ mock_tts() → sine wave PCM 16kHz mono int16
   ↓ AudioSource.capture_frame
LiveKit room (publish agent track)
```

## 文件结构

```
agent-worker/
├── Dockerfile
├── requirements.txt
├── README.md                ← 本文件
└── app/
    ├── __init__.py
    ├── main.py              ← 入口 + Agent 类 + LiveKit I/O + Pipeline
    ├── state_machine.py     ← FSM with 合法转移检查
    ├── vad.py               ← onnxruntime 直加载 silero VAD
    ├── stt_client.py        ← WS 客户端（连 stt-server）
    ├── llm_client.py        ← OpenAI 兼容客户端（连 llm-server）
    ├── tts_client.py        ← HTTP 流式客户端（连 tts-server）
    ├── phrase_split.py      ← LLM token 流 → phrase async generator (v0.5.1)
    └── mock_pipeline.py     ← 已废弃，待 v0.6 删除
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `LIVEKIT_API_KEY` | - | 必填 |
| `LIVEKIT_API_SECRET` | - | 必填 |
| `LIVEKIT_INTERNAL_URL` | `ws://livekit-server:7880` | docker network 内地址 |
| `STT_WS_URL` | `ws://stt-server:9090/asr` | STT 服务 WebSocket 地址 |
| `STT_FINAL_TIMEOUT_S` | `5.0` | 等 STT final 的超时 |
| `LLM_BASE_URL` | `http://llm-server:11434/v1` | OpenAI 兼容 API 端点 |
| `LLM_MODEL` | `qwen2.5:1.5b` | LLM 模型 ID（ollama / vLLM 都用） |
| `LLM_API_KEY` | `ollama` | ollama 不验证；OpenAI SDK 必需非空 |
| `TTS_BASE_URL` | `http://tts-server:9880` | TTS 服务 HTTP 地址 |
| `TTS_VOICE` | `zf_xiaobei` | Kokoro 音色 ID |
| `TTS_LANG` | `cmn` | espeak-ng 语言代码 |
| `TTS_PIPELINE_CONCURRENCY` | `2` | 并行 TTS 任务数（v0.5.1） |
| `AGENT_ROOM` | `rtvoice-test` | agent 加入哪个 room |
| `AGENT_IDENTITY` | `rtvoice-agent` | agent 标识 |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR |

## 状态机

参考 [ARCHITECTURE.md §5](../../ARCHITECTURE.md#5-agent-状态机)：

```
Idle → Listening → Thinking → Speaking → Idle
                                  ↓
                             Interrupted → Listening
```

`state_machine.py` 内置非法转移检查：触发非法转移会打 warning 但不抛异常（保护活跃对话）。

## VAD 调参

`app/vad.py` 顶部常量：

| 参数 | 默认 | 调整建议 |
|---|---|---|
| `SPEECH_THRESHOLD` | 0.5 | 噪音多 → 调高到 0.6-0.7 |
| `SILENCE_END_MS` | 600 | 用户说话停顿多 → 加到 800-1000 |
| `WINDOW_SIZE_SAMPLES` | 512 (32ms@16k) | silero v5 固定，不要改 |

## v0.2 已知限制

- **TTS 是 sine wave**：听感差。v0.4 换 Kokoro CPU 或 v0.5 换 CosyVoice 2 GPU
- **mock LLM 仅关键词匹配**：5 句固定回复，没有真上下文
- **未在真音频环境测过**：playwright headless 无 mic，e2e 验证步骤见 [docs/v0.2-validation.md](../../docs/v0.2-validation.md)
- **单 room 单 agent**：v1 不支持一个 worker 服务多个房间

## 演进路线

| 版本 | 变化 | 状态 |
|---|---|---|
| v0.3 | STT 切独立 stt-server（sherpa-onnx Streaming Zipformer CPU），WS 协议 | ✅ |
| v0.4 | LLM 切独立 llm-server（ollama + Qwen2.5-1.5B CPU），OpenAI 兼容流式 | ✅ |
| v0.5 | TTS 切独立 tts-server（Kokoro 82M ONNX CPU），HTTP 流式 chunked | ✅ |
| v0.5.1 | LLM 流式 → 句切分 → 并发 TTS pipeline（首包延迟降~1-3s） | ✅ |
| v0.5+ | docker-compose.prod.yml：vLLM + CosyVoice 2 GPU + sherpa-onnx GPU | ⏳ |
| v0.6 | 迁移到 livekit-agents AgentSession 框架；接入框架的 turn detection | ⏳ |

## v0.5.1 Pipeline 设计

```
STT final text
   ↓
LLM.stream(text) ──→ token deltas
   ↓
phrase_split.stream_to_phrases() ──→ phrase
   ↓ (按到达即 fire create_task，受 SEM 限流)
[TTS task 1] [TTS task 2] [TTS task 3] ...   并发合成
   ↓ (严格按 phrase 顺序 await)
publisher: pull → publish 20ms 帧到 LiveKit
```

**关键时序**：
- 第一个 phrase 一就绪就转 SPEAKING，开播
- producer 边收 LLM 边切片边 fire TTS（不等整段 LLM）
- consumer 严格按入队顺序 await，保证音频顺序正确

**指标日志**（每轮对话末尾）：
```
[ROUND METRIC] {'phrases': 2, 'first_phrase_ready_ms': 850, 'first_audio_ms': 1450, 'round_ms': 4200}
```
- `first_phrase_ready_ms`：LLM 开始 → 首个 TTS 完成（含网络）
- `first_audio_ms`：用户说完 → 首字音频送出
- `round_ms`：用户说完 → agent 说完
- 这是 v0.6 真实测延迟的基础
