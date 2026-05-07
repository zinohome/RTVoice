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
| [ENGINES.md](./ENGINES.md) | 引擎选型：STT/TTS/LLM 候选对比、选型理由、降级策略 |
| [CHANGELOG.md](./CHANGELOG.md) | 版本历史：v0.0-v0.5.1 详细 release notes + 经验教训摘录 |
| [monitoring/README.md](./monitoring/README.md) | 可选 Prometheus + Grafana 监控栈（21 panel dashboard） |

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

## Quick Start

需要 Docker 24+（含 compose v2）。**不需要** Python / NodeJS / NVIDIA 驱动（dev 是 CPU）。

```bash
# 1. 克隆 + 进入目录
git clone git@github.com:zinohome/RTVoice.git && cd RTVoice

# 2. 配置环境变量
cp .env.example .env
# 生成两个强密钥（任一发行版有 python3 即可）：
echo "LIVEKIT_API_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env.tmp
echo "APP_API_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env.tmp
# 把上面两行填回 .env，覆盖默认值，删掉 .env.tmp

# 3. 一键启动（首次会拉取镜像 + ollama 拉 Qwen2.5-1.5B 约 5 分钟）
./scripts/dev-up.sh

# 4. 浏览器测试
# 打开 http://127.0.0.1:8000，点"加入房间" → "开麦克风" → 说话
# (DEV_AUTO_INJECT_KEY=true 会自动填 API key 到表单)

# 5. 停止
./scripts/dev-down.sh                # 仅停容器，保留模型卷（推荐）
./scripts/dev-down.sh --wipe         # 连模型一起删（需输入 YES I AM SURE）
```

### 验证服务健康

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml ps
# 期望看到 6 个服务全 healthy:
#   livekit-server / token-server / stt-server / llm-server / tts-server / agent-worker
```

### 看 agent 实时日志

```bash
docker logs -f rtvoice-agent
# 期望事件流（说话 → agent 回复）：
#   [VAD] speech_start
#   [STT final] '<你说的话>'
#   [phrase 1] '<LLM 第一句回复>'
#   [ROUND METRIC] {phrases: 2, first_audio_ms: 1450, round_ms: 4200}
```

### 生产部署（RTX 3060 12GB 服务器）

详细步骤见 [DEPLOY.md](./DEPLOY.md)。简版：

```bash
# 1. 在生产服务器上 git pull 此仓库 + 配置 .env（强随机 + 公网 URL）
# 2. 只读探查
./scripts/prod-deploy.sh --inspect
# 3. 备份现有数据卷
./scripts/prod-deploy.sh --backup
# 4. 拉镜像 + 渐进部署（每服务 healthcheck 后再继续）
./scripts/prod-deploy.sh --apply
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
