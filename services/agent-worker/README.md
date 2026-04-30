# agent-worker

**职责**：加入 LiveKit 房间，订阅用户音频，跑 VAD + 状态机，驱动 STT → LLM → TTS 流水线。

**状态**：✅ v0.3（STT 切到独立 stt-server；LLM/TTS 仍 mock）

## 技术栈

| 组件 | 选型 | 备注 |
|---|---|---|
| LiveKit 客户端 | `livekit==1.0.16` (rtc) + `livekit-api==1.1.0` | 低层 rtc.Room API |
| VAD | `onnxruntime` + 手动下载 silero VAD ONNX (~2MB) | **不用 silero-vad pip 包**（拖 torch 12GB） |
| STT | WS 客户端 → stt-server (sherpa-onnx Streaming Zipformer) | 长连接，PCM 流式 + EOS |
| LLM/TTS | mock 内嵌（in-process） | v0.4 LLM, v0.5 TTS |
| WebSocket | `websockets==13.1` | STT 客户端依赖 |

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
    └── mock_pipeline.py     ← mock LLM / TTS（mock_stt 已废弃）
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `LIVEKIT_API_KEY` | - | 必填 |
| `LIVEKIT_API_SECRET` | - | 必填 |
| `LIVEKIT_INTERNAL_URL` | `ws://livekit-server:7880` | docker network 内地址 |
| `STT_WS_URL` | `ws://stt-server:9090/asr` | STT 服务 WebSocket 地址 |
| `STT_FINAL_TIMEOUT_S` | `5.0` | 等 STT final 的超时 |
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
| v0.4 | LLM 切到 Ollama（Qwen2.5-1.5B CPU），HTTP/SSE 流式 | ⏳ |
| v0.5 | 切 prod profile：vLLM + CosyVoice 2 GPU + sherpa-onnx GPU | ⏳ |
| v0.6 | 迁移到 livekit-agents AgentSession 框架；接入框架的 turn detection | ⏳ |
