# RTVoice

本地部署的实时语音对话系统（Voice Agent），目标场景类似 ChatGPT 语音模式：低延迟、流式、可打断。

- **STT**：sherpa-onnx + Paraformer 中文流式
- **TTS**：CosyVoice 2-0.5B 流式（生产）/ mock（开发）
- **LLM**：Qwen2.5-3B（生产）/ mock 或 Ollama 1.5B（开发）
- **编排**：livekit-agents + LiveKit WebRTC SFU
- **部署**：Docker Compose，dev/prod profile 切换
- **目标硬件**：单卡 NVIDIA RTX 3060 12GB

> **状态：v0 文档与脚手架阶段**，尚未启动任何服务。

---

## 文档（动手前请按顺序读）

| 文档 | 内容 |
|---|---|
| [SECURITY.md](./SECURITY.md) | 安全契约：禁止行为、生产迁移协议、回滚策略 |
| [DEPLOY.md](./DEPLOY.md) | 部署手册：开发机 / 生产机流程、备份、故障排查 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 系统设计：组件、数据流、状态机、性能预算、ADR |

---

## 目录结构

```
RTVoice/
├── SECURITY.md
├── DEPLOY.md
├── ARCHITECTURE.md
├── README.md
├── .gitignore
├── .env.example
├── docker-compose.yml             # (TODO) 公共定义
├── docker-compose.dev.yml         # (TODO) 开发覆盖
├── docker-compose.prod.yml        # (TODO) 生产覆盖
├── services/
│   ├── token-server/              # FastAPI，发 LiveKit JWT
│   ├── agent-worker/              # livekit-agents Python worker
│   ├── stt-server/                # sherpa-onnx 流式 ASR 服务
│   └── tts-server/                # CosyVoice 流式 TTS 服务
├── livekit/                       # livekit-server 配置
├── scripts/                       # 部署/备份脚本
└── docs/                          # 补充文档
```

---

## 快速开始（开发机）

> ⚠️ 当前阶段服务尚未实现，下面是规划中的命令。

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 LIVEKIT_API_KEY/SECRET

# 2. 启动开发栈（CPU + mock 引擎）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# 3. 查看状态
docker compose ps
docker compose logs -f agent-worker

# 4. 浏览器测试（待前端就位）
# 打开 http://127.0.0.1:8000

# 5. 停止
docker compose down
```

---

## 演进路线

| 版本 | 范围 | 状态 |
|---|---|---|
| v0.0 | 文档三件套 + 脚手架 | ✅ |
| v0.1 | LiveKit + token-server，浏览器加入房间 | ⏳ |
| v0.2 | mock STT/TTS/LLM 跑通状态机 | ⏳ |
| v0.3 | 接 sherpa-onnx CPU 实 STT | ⏳ |
| v0.4 | 接 Ollama / Kokoro 真引擎（CPU 可跑） | ⏳ |
| v0.5 | 生产覆盖：vLLM + CosyVoice 2 + sherpa GPU | ⏳ |
| v0.6 | 生产机首次部署 + 性能调优 | ⏳ |

详见 [ARCHITECTURE.md §11](./ARCHITECTURE.md)。

---

## 关键决策摘要

- **为什么 livekit-agents 而非 pipecat**：WebRTC 传输层抗弱网，用户场景要求远程访问鲁棒性
- **为什么 CosyVoice 2 而非 GPT-SoVITS**：流式 TTFB 150ms，3060 跑得动；voice agent 流式是必选项
- **为什么独立成多服务**：模型加载慢、依赖冲突大、独立崩溃恢复
- **为什么开发机 mock 而非接生产 GPU**：安全契约；开发污染不影响生产

完整 ADR 见 [ARCHITECTURE.md §12](./ARCHITECTURE.md)。

---

## 性能目标

| 指标 | 目标（生产） |
|---|---|
| 端到端延迟 p95 | ≤ 1.2s |
| TTS 首包 | ≤ 300ms |
| Barge-in 响应 | ≤ 200ms |
| 总显存 | ≤ 10GB |

---

## 安全提示

- 所有服务**默认绑定 `127.0.0.1`**，公网暴露需在 `.env` 显式开启
- `.env` **绝不提交**，已在 `.gitignore`
- LiveKit API Secret 在生产机和开发机**不要复用**
- 生产机操作前必读 [SECURITY.md](./SECURITY.md)
