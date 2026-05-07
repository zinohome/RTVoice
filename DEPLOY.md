# RTVoice 部署手册

本文档说明 RTVoice voice services platform 从开发机到生产机的部署流程、目录约定、配置切换方式与运维操作。
**部署前必读 [SECURITY.md](./SECURITY.md)**。

---

## 1. 环境矩阵

| 维度 | 开发机（当前） | 生产机（目标） |
|---|---|---|
| OS | Linux | Linux |
| GPU | 无 | NVIDIA RTX 3060 12GB |
| NVIDIA 驱动 | 不需要 | 必须装 |
| nvidia-container-toolkit | 不需要 | 必须装 |
| Docker | 29.x | ≥ 24.x |
| Docker Compose | v2 plugin | v2 plugin |
| Compose profile | `dev` | `prod` |
| STT 引擎 | sherpa-onnx CPU + 中文小模型 | sherpa-onnx GPU 或 SenseVoice |
| TTS 引擎 | mock / Kokoro CPU | CosyVoice 2 GPU |
| LLM | Ollama CPU + 1.5B / mock | vLLM GPU + Qwen2.5-3B/7B |

---

## 2. 项目目录结构

```
RTVoice/
├── SECURITY.md                  # 安全契约（必读）
├── DEPLOY.md                    # 本文件
├── README.md
├── .env.example                 # 环境变量模板（提交）
├── .env                         # 实际配置（不提交，.gitignore）
├── .gitignore
├── docker-compose.yml           # 公共定义
├── docker-compose.dev.yml       # 开发覆盖（CPU/mock）
├── docker-compose.prod.yml      # 生产覆盖（GPU/真引擎）
├── services/
│   ├── token-server/            # FastAPI，发 LiveKit JWT
│   │   ├── Dockerfile
│   │   └── ...
│   ├── agent-worker/            # livekit-agents Python worker
│   │   ├── Dockerfile
│   │   ├── plugins/
│   │   │   ├── stt_sherpa.py    # sherpa-onnx STT 适配
│   │   │   └── tts_cosyvoice.py # CosyVoice TTS 适配
│   │   └── ...
│   ├── stt-server/              # 包 sherpa-onnx 成 WS 服务
│   │   └── Dockerfile
│   └── tts-server/              # 包 CosyVoice 成 HTTP 流式服务
│       └── Dockerfile
├── livekit/
│   └── livekit.yaml             # LiveKit 配置（dev/prod 各一份）
├── docs/
│   ├── architecture.md
│   └── ...
└── scripts/
    ├── dev-up.sh                # 开发机一键起
    ├── prod-deploy.sh           # 生产部署（需用户确认）
    └── backup-volumes.sh        # 卷备份脚本
```

---

## 3. 环境变量约定

`.env` 不进版本控制。`.env.example` 提交到仓库作为模板。

```ini
# === 公共 ===
COMPOSE_PROJECT_NAME=rtvoice
LIVEKIT_API_KEY=          # 用 livekit-cli generate-keys 生成
LIVEKIT_API_SECRET=

# === 开发机（dev profile） ===
# 端口默认绑 127.0.0.1，仅本机访问
BIND_HOST=127.0.0.1
LIVEKIT_PORT=7880
TOKEN_SERVER_PORT=8000

# === 生产机（prod profile） ===
# 是否对外暴露由用户在生产机 .env 显式配置
# BIND_HOST=0.0.0.0          # ← 仅当用户确认要公网时开启
# GPU 设备 ID（默认仅 0 号卡）
GPU_DEVICE_IDS=0
```

---

## 4. 开发机：日常使用

### 4.1 首次启动

```bash
cp .env.example .env
# 编辑 .env，至少生成 LIVEKIT_API_KEY / SECRET
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile dev build
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile dev up -d
```

或使用脚本：

```bash
./scripts/dev-up.sh
```

### 4.2 查看状态

```bash
docker compose ps
docker compose logs -f agent-worker
```

### 4.3 停止

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml down
```

> ⚠️ 不要带 `-v`，否则会删数据卷。除非你真的要重置开发数据。

### 4.4 开发机限制

- TTS 是 mock，输出预录音频或简单 sine wave
- LLM 是 mock 或 Ollama 小模型，回复质量不代表生产
- STT 用 CPU 版 sherpa-onnx + tiny 模型，准确率比生产差
- 这些都是为了在无 GPU 环境验证**链路与状态机**，不是验证**模型质量**

---

## 5. 生产机：首次部署

### 5.1 前置检查（用户操作）

生产机上必须满足：

```bash
nvidia-smi                                    # 看到 RTX 3060 12GB
docker --version                              # ≥ 24
docker compose version                        # v2 plugin
docker info | grep -i runtime                 # 看到 nvidia
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi   # 容器内能看到 GPU
```

如有任一不满足，停下来安装/修复，不要往下走。

### 5.2 部署前快照

无论生产机现状如何，先备份：

```bash
docker ps -a > /tmp/rtvoice-pre-deploy-$(date +%F).txt
docker images > /tmp/rtvoice-pre-deploy-images-$(date +%F).txt
docker volume ls > /tmp/rtvoice-pre-deploy-volumes-$(date +%F).txt
```

### 5.3 拉取制品

两种方式选一：

**方式 A：镜像仓库**

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
```

**方式 B：离线 tar**

```bash
# 开发机
docker save rtvoice/agent-worker:v0.1.0 rtvoice/stt-server:v0.1.0 \
  rtvoice/tts-server:v0.1.0 rtvoice/token-server:v0.1.0 \
  | gzip > rtvoice-images-v0.1.0.tar.gz

# 生产机
gunzip -c rtvoice-images-v0.1.0.tar.gz | docker load
```

### 5.4 配置 .env

```bash
cp .env.example .env
# 编辑：
# - 重新生成 LIVEKIT_API_KEY/SECRET（不要复用开发机的）
# - 设置 BIND_HOST 与端口暴露策略
# - 设置 GPU_DEVICE_IDS
```

### 5.5 启动

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod up -d
```

逐服务等待 healthcheck：

```bash
watch -n 2 'docker compose ps'
```

### 5.6 验证

按顺序验证：

1. `docker compose logs livekit-server` 没报错
2. `docker compose logs token-server` 健康
3. 用 livekit-cli 或浏览器测试加入房间
4. agent worker 日志看到 `agent joined room`
5. 实际跑一段对话，看端到端延迟

### 5.7 失败回滚

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod down
# 改回上一版本 tag
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod up -d
```

---

## 6. 生产机：升级流程

```bash
# 1. 备份卷
./scripts/backup-volumes.sh

# 2. 拉新镜像（不重启）
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull

# 3. 滚动更新（一次一个服务）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps <service>

# 4. 验证 healthcheck
docker compose ps <service>

# 5. 全部更新完，5 分钟监控
docker compose logs -f --tail=100
```

---

## 7. 备份与恢复

### 7.1 备份所有数据卷

```bash
./scripts/backup-volumes.sh
# 产出：backups/<vol-name>-<timestamp>.tar.gz
```

### 7.2 恢复某个卷

```bash
docker volume create rtvoice_models   # 如不存在
docker run --rm -v rtvoice_models:/data -v $(pwd)/backups:/backup alpine \
  sh -c 'cd /data && tar xzf /backup/rtvoice_models-2026-04-29.tar.gz --strip-components=1'
```

---

## 8. 故障排查清单

| 症状 | 检查 |
|---|---|
| GPU 不可见 | `docker exec <agent> nvidia-smi`；compose 文件 `deploy.resources.reservations.devices` |
| LiveKit 连不上 | UDP 50000-60000 端口开放？防火墙？BIND_HOST 是否对外？ |
| 延迟高 | 看每个组件单独耗时；TTS 是不是没走流式？LLM 首 token 多久？ |
| OOM | `docker stats`；调小模型或量化；mem_limit 是否合理 |
| 模型下载失败 | 国内镜像源（HF mirror）；离线分发模型卷 |

---

## 9. 联系点

- 安全问题（疑似破坏行为/数据丢失风险）：立即停止，找用户
- 性能问题：看监控日志，再讨论调优
- 模型选型变更：先在开发机 mock 验证管道，再换生产真模型
