# RTVoice 智能体部署手册

本文档为 AI 智能体设计，格式严格、无歧义，确保一次部署成功。

---

## 前置条件核查清单（执行前必须逐项确认）

```bash
# 1. GPU 可访问
nvidia-smi

# 2. Docker 可用
docker info

# 3. NVIDIA Container Toolkit 正常
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi

# 4. ollama 已安装并拉取模型（LLM 用宿主 ollama）
ollama list | grep qwen2.5:7b
# 如未拉取：ollama pull qwen2.5:7b

# 5. RTVoice 仓库已 clone
ls /data/RTVoice/deployment/docker-compose.yml
```

**若任一条件不满足，先修复后再继续。**

---

## 部署步骤（按顺序执行，不可跳步）

### STEP 1：进入 deployment 目录

```bash
cd /data/RTVoice/deployment
```

### STEP 2：创建 .env（首次部署）

```bash
cp .env.example .env
```

### STEP 3：填写 .env（以下所有项必须设置）

使用 `sed -i` 批量替换（将示例值替换为实际值）：

```bash
# 必填：服务器 IP（替换为实际 IP）
sed -i 's/^SERVER_IP=.*/SERVER_IP=192.168.66.163/' .env

# 必填：LIVEKIT_PUBLIC_URL（替换 IP）
sed -i 's|^LIVEKIT_PUBLIC_URL=.*|LIVEKIT_PUBLIC_URL=ws://192.168.66.163:7880|' .env

# 必填：生成并填写密钥（使用 Python 生成随机值）
LIVEKIT_KEY="rtvoice-$(python3 -c 'import secrets; print(secrets.token_urlsafe(8))')"
LIVEKIT_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
APP_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
RTVOICE_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
ADMIN_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"
SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
TTS_ADMIN_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

sed -i "s/^LIVEKIT_API_KEY=.*/LIVEKIT_API_KEY=${LIVEKIT_KEY}/" .env
sed -i "s/^LIVEKIT_API_SECRET=.*/LIVEKIT_API_SECRET=${LIVEKIT_SECRET}/" .env
sed -i "s/^APP_API_KEY=.*/APP_API_KEY=${APP_KEY}/" .env
sed -i "s/^RTVOICE_API_KEY=.*/RTVOICE_API_KEY=${RTVOICE_KEY}/" .env
sed -i "s/^RTVOICE_ADMIN_PASSWORD=.*/RTVOICE_ADMIN_PASSWORD=${ADMIN_PASS}/" .env
sed -i "s/^RTVOICE_SESSION_SECRET=.*/RTVOICE_SESSION_SECRET=${SESSION_SECRET}/" .env
sed -i "s/^TTS_ADMIN_API_KEY=.*/TTS_ADMIN_API_KEY=${TTS_ADMIN_KEY}/" .env
```

### STEP 4：确保 1panel-network 存在

```bash
docker network inspect 1panel-network > /dev/null 2>&1 || docker network create 1panel-network
```

### STEP 5：创建数据目录并修复权限

```bash
# 创建目录并设置 777 权限（容器以非 root 用户运行，需要写权限）
for dir in keys transcripts cosyvoice-models caddy/data caddy/config prometheus grafana; do
  mkdir -p /data/RTVoice/$dir
  chmod 777 /data/RTVoice/$dir
done
```

### STEP 6：初始化 API Keys

```bash
# 创建空列表；容器启动时自动将 RTVOICE_API_KEY 迁移为正确格式（含 id + secret_hash）
printf 'keys: []\n' > /data/RTVoice/keys/keys.yaml
chmod 666 /data/RTVoice/keys/keys.yaml
```

### STEP 7：构建 Docker 镜像

```bash
# 此步骤需要 15-60 分钟（首次），请耐心等待
docker compose \
  --project-directory /data/RTVoice/deployment \
  -f /data/RTVoice/deployment/docker-compose.yml \
  --env-file /data/RTVoice/deployment/.env \
  build --progress=plain
```

**构建成功标志**：每个服务输出 `Successfully built` 或 `writing image`，无 `ERROR` 行。

### STEP 8：启动服务

```bash
docker compose \
  --project-directory /data/RTVoice/deployment \
  -f /data/RTVoice/deployment/docker-compose.yml \
  --env-file /data/RTVoice/deployment/.env \
  up -d
```

### STEP 9：验证部署

```bash
# 等待核心服务启动（最多 3 分钟）
sleep 60

# 检查服务状态
docker compose \
  --project-directory /data/RTVoice/deployment \
  -f /data/RTVoice/deployment/docker-compose.yml \
  --env-file /data/RTVoice/deployment/.env \
  ps

# 核心服务健康检查（必须全部 healthy）
for c in rtvoice-livekit rtvoice-token rtvoice-stt rtvoice-realtime rtvoice-caddy; do
  status=$(docker inspect --format='{{.State.Health.Status}}' $c 2>/dev/null || echo "not_found")
  echo "$c: $status"
done
```

**成功标志**：上述核心容器全部显示 `healthy`。

**注意**：`rtvoice-tts` 首次启动会下载 ~5.6GB 模型，可能需要额外 10-30 分钟，这是正常的。
```bash
# 监控 TTS 下载进度
docker logs --tail 20 rtvoice-tts
```

---

## 部署后验证

```bash
SERVER_IP="$(grep '^SERVER_IP=' /data/RTVoice/deployment/.env | cut -d= -f2)"

# 获取 CA 证书（用于 curl HTTPS 测试）
docker exec rtvoice-caddy cat /data/caddy/pki/authorities/local/root.crt > /tmp/rtvoice-ca.crt

# 测试 HTTPS（realtime 健康）
curl -s --cacert /tmp/rtvoice-ca.crt "https://${SERVER_IP}/health" | grep -c '"status":"ok"' && echo "HTTPS OK"

# 测试 Admin Console 可访问
curl -sk --cacert /tmp/rtvoice-ca.crt "https://${SERVER_IP}/admin/login" | grep -c "RTVoice" && echo "Admin UI OK"
```

---

## 关键信息汇总（保存到 issue 评论）

部署完成后，向用户汇报以下信息：

```
RTVoice 部署完成
- Admin Console: https://SERVER_IP/admin/
- Admin 用户名: admin（密码在 .env RTVOICE_ADMIN_PASSWORD）
- Grafana 监控: http://SERVER_IP:13000（初始 admin/admin）
- CA 证书: ssh root@SERVER_IP 'docker exec rtvoice-caddy cat /data/caddy/pki/authorities/local/root.crt' > rtvoice-ca.crt
- 数据目录: /data/RTVoice/
- TTS 状态: docker logs -f rtvoice-tts
```

---

## 更新已有部署

```bash
cd /data/RTVoice

# 拉取最新代码
git pull

# 重建有变更的镜像
cd deployment
docker compose \
  --project-directory /data/RTVoice/deployment \
  -f docker-compose.yml --env-file .env \
  build --progress=plain

# 重启更新的服务（数据保留）
docker compose \
  --project-directory /data/RTVoice/deployment \
  -f docker-compose.yml --env-file .env \
  up -d
```

---

## 错误处理

| 错误 | 原因 | 修复 |
|------|------|------|
| `1panel-network not found` | 1Panel 未启动或未创建网络 | `docker network create 1panel-network` |
| `RTVOICE_SESSION_SECRET 必须设置` | .env 缺少该字段 | 按 STEP 3 填写 |
| TTS 容器 OOM/Exit | 显存不足 | 减小 `TTS_MEM_LIMIT=8G`，确认 ollama 不占用过多显存 |
| `nvidia-container-cli`: GPU 错误 | NVIDIA Container Toolkit 未配置 | 重装 toolkit 并重启 Docker |
| Caddy healthcheck 失败 | 上游服务未就绪 | 等待 realtime/token/stt/tts 全部 healthy |
