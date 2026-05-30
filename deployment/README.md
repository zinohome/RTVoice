# RTVoice 一键部署指南

适用目标环境：安装了 **1Panel + NVIDIA GPU + Docker** 的 Linux 服务器（如 RTX 3060 12GB）。

---

## 快速开始（5 步）

### 第 1 步：克隆仓库

```bash
cd /data
git clone https://github.com/zinohome/RTVoice.git
cd RTVoice/deployment
```

### 第 2 步：生成密钥

```bash
bash scripts/gen-secrets.sh
```

将输出复制，用于下一步填入 `.env`。

### 第 3 步：配置环境变量

```bash
cp .env.example .env
nano .env    # 或 vim .env
```

**必须修改的项：**

| 变量 | 说明 | 示例 |
|------|------|------|
| `SERVER_IP` | 服务器局域网 IP | `192.168.66.163` |
| `LIVEKIT_API_KEY` | LiveKit 密钥（gen-secrets.sh 输出） | `rtvoice-abc123` |
| `LIVEKIT_API_SECRET` | LiveKit 密钥 | （gen-secrets.sh 输出） |
| `APP_API_KEY` | Token Server 鉴权 Key（≥32字符） | （gen-secrets.sh 输出） |
| `RTVOICE_API_KEY` | 内部服务鉴权 Key（≥32字符） | （gen-secrets.sh 输出） |
| `RTVOICE_ADMIN_PASSWORD` | Admin Console 登录密码 | （gen-secrets.sh 输出） |
| `RTVOICE_SESSION_SECRET` | Cookie 签名密钥 | （gen-secrets.sh 输出） |
| `LIVEKIT_PUBLIC_URL` | 浏览器连接 LiveKit 的 URL | `ws://192.168.66.163:7880` |

> **ollama 依赖**：LLM 使用宿主 ollama，请先在服务器上安装并拉取模型：
> ```bash
> ollama pull qwen2.5:7b
> ```

### 第 4 步：一键部署

```bash
bash scripts/deploy.sh
```

脚本会依次执行：前置检查 → 创建数据目录 → 构建镜像 → 启动服务。

**首次构建约需 20-60 分钟**（TTS CosyVoice3 镜像较大），有缓存后重部署约 5 分钟。

**TTS 首次启动会下载约 5.6GB 模型**，观察进度：
```bash
docker logs -f rtvoice-tts
```

### 第 5 步：信任 CA 证书（客户端）

Caddy 使用自签 CA，浏览器首次访问需信任：

```bash
# 在服务器上执行，获取 CA 证书
docker exec rtvoice-caddy cat /data/caddy/pki/authorities/local/root.crt > rtvoice-ca.crt

# macOS（发送到需要访问的客户端）
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain rtvoice-ca.crt

# Linux
sudo cp rtvoice-ca.crt /usr/local/share/ca-certificates/rtvoice-ca.crt
sudo update-ca-certificates

# Windows
# 双击 rtvoice-ca.crt → 安装证书 → 本地计算机 → 受信任的根证书颁发机构
```

---

## 访问地址

| 服务 | 地址 |
|------|------|
| Admin Console | `https://SERVER_IP/admin/` |
| Grafana 监控 | `http://SERVER_IP:13000`（默认 admin/admin） |
| LiveKit | `ws://SERVER_IP:7880` |

---

## 1Panel 编排集成

部署完成后，RTVoice 会自动出现在 **1Panel → 编排** 中（`name: rtvoice`）。

若要通过 1Panel 从 Git 仓库管理：
1. 1Panel → 编排 → 新建 → 选择 "Git 仓库"
2. 仓库地址：`https://github.com/zinohome/RTVoice.git`
3. Compose 文件路径：`deployment/docker-compose.yml`
4. 环境变量：将 `.env` 内容粘贴到 1Panel 的环境变量编辑器

---

## 数据目录说明

所有数据存储在 `/data/RTVoice/`：

```
/data/RTVoice/
├── keys/                   # API Key 配置（keys.yaml）
├── transcripts/            # 对话记录（JSONL）
├── cosyvoice-models/       # TTS 模型（~5.6GB，首次自动下载）
├── caddy/
│   ├── data/               # Caddy TLS 证书（含 CA 私钥）
│   └── config/             # Caddy 运行时配置
├── prometheus/             # Prometheus 数据
└── grafana/                # Grafana 数据库
```

**重要**：`/data/RTVoice/caddy/data/` 包含 TLS 私钥，请定期备份。

---

## 常用运维命令

```bash
# 进入 deployment 目录
cd /data/RTVoice/deployment

# 查看服务状态
docker compose -f docker-compose.yml ps

# 查看日志（所有服务）
docker compose -f docker-compose.yml logs -f

# 查看单个服务日志
docker logs -f rtvoice-tts

# 重启单个服务（不重新构建）
docker compose -f docker-compose.yml restart tts-server

# 停止所有服务（保留数据）
docker compose -f docker-compose.yml down

# 更新后重新部署
git pull
docker compose -f docker-compose.yml build tts-server   # 仅重建有变更的服务
docker compose -f docker-compose.yml up -d tts-server
```

---

## 自定义音色

1. 登录 Admin Console：`https://SERVER_IP/admin/`
2. 进入 **Voice Keys** → **注册音色**
3. 上传参考音频（3-30 秒清晰人声 wav）和对应文本
4. 系统自动规范化（转 16kHz mono，取 8 秒）

**前提**：`.env` 中 `TTS_ADMIN_API_KEY` 需已设置。

---

## 故障排查

| 问题 | 解决方法 |
|------|---------|
| TTS 无声音 | `docker logs rtvoice-tts`，检查是否 OOM；尝试调小 `TTS_MEM_LIMIT` |
| TTS 显存 OOM | 确认 ollama 模型已卸载或配置较小的 LLM；`TTS_MEM_LIMIT=8G` |
| WebSocket 连接失败 | 确认 `LIVEKIT_PUBLIC_URL` 中的 IP 是客户端可达的服务器 IP |
| HTTPS 证书错误 | 按"第 5 步"信任 CA 证书 |
| Admin Console 登录失败 | 检查 `RTVOICE_ADMIN_USERNAME` 和 `RTVOICE_ADMIN_PASSWORD` |
