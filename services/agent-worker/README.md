# agent-worker

**职责**：livekit-agents Python worker。加入 LiveKit 房间，运行编排状态机：VAD → STT → LLM → TTS，处理 turn-taking 和 barge-in。

**技术栈**：Python 3.11 + livekit-agents + silero-vad

**GPU**：不需要（VAD 用 CPU，模型在外部服务）

**依赖外部服务**：`stt-server`, `tts-server`, `llm-server`, `livekit-server`

**关键状态机**：见 [ARCHITECTURE.md §5](../../ARCHITECTURE.md#5-agent-状态机)

**待实现**：v0.1（先做 join + echo），v0.2（接 mock 引擎跑通状态机）

## 计划目录结构

```
agent-worker/
├── Dockerfile
├── pyproject.toml
├── src/
│   ├── main.py                # entrypoint
│   ├── agent.py               # VoiceAgent 主类
│   ├── state_machine.py       # 状态机
│   └── plugins/
│       ├── stt_sherpa.py      # STT 适配器（调 stt-server）
│       ├── tts_cosyvoice.py   # TTS 适配器（调 tts-server）
│       └── llm_openai_compat.py  # LLM 适配器（OpenAI 兼容）
└── tests/
```
