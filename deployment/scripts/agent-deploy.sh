#!/usr/bin/env bash
# agent-deploy.sh — RTVoice 智能体版全自动部署脚本
#
# 设计原则：
#   - 完全非交互式（无 read/confirm），适合 AI Agent 通过 SSH 执行
#   - 有错立刻退出（set -euo pipefail）
#   - 每步打印清晰状态，便于 Agent 解析结果
#   - 幂等：多次运行结果一致，不会破坏已有数据
#
# 前置条件（Agent 执行前必须确认）：
#   1. 目标服务器已安装 Docker + NVIDIA Container Toolkit
#   2. 宿主已安装 ollama 并拉取 qwen2.5:7b（或 LLM_MODEL_PROD 指定的模型）
#   3. 1Panel 已安装（1panel-network 将自动创建）
#   4. 已 git clone 完整 RTVoice 仓库到服务器
#   5. deployment/.env 已按模板填写所有必填项
#
# 用法（Agent 通过 SSH 到部署服务器执行）：
#   REPO_PATH=/data/RTVoice bash /data/RTVoice/deployment/scripts/agent-deploy.sh
#
# 环境变量（可覆盖默认值）：
#   REPO_PATH   — RTVoice 仓库路径（默认 /data/RTVoice）
#   SKIP_BUILD  — 设为 1 跳过镜像构建（镜像已存在时用）
#   DATA_BASE   — 数据目录基路径（默认 /data/RTVoice）

set -euo pipefail

REPO_PATH="${REPO_PATH:-/data/RTVoice}"
DATA_BASE="${DATA_BASE:-/data/RTVoice}"
DEPLOY_DIR="${REPO_PATH}/deployment"
SKIP_BUILD="${SKIP_BUILD:-0}"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo "[$(date '+%H:%M:%S')] ✅ $*"; }
fail() { echo "[$(date '+%H:%M:%S')] ❌ $*" >&2; exit 1; }

log "=== RTVoice Agent Deploy 开始 ==="
log "REPO_PATH=${REPO_PATH}"
log "DATA_BASE=${DATA_BASE}"

# ─── 环境检查 ─────────────────────────────────────────────────
log "--- [1/7] 环境检查 ---"

[[ -d "${DEPLOY_DIR}" ]] || fail "deployment/ 目录不存在：${DEPLOY_DIR}。请先 git clone RTVoice 仓库"
[[ -f "${DEPLOY_DIR}/.env" ]] || fail ".env 不存在：${DEPLOY_DIR}/.env。请先 cp .env.example .env 并填写"

cd "${DEPLOY_DIR}"

# 读取环境变量（不使用 source 避免污染环境）
get_env() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'"; }

SERVER_IP="$(get_env SERVER_IP)"
[[ -n "${SERVER_IP}" ]] || fail "SERVER_IP 未设置"
[[ "${SERVER_IP}" != "192.168.X.X" ]] || fail "SERVER_IP 仍是示例值"

for key in LIVEKIT_API_KEY LIVEKIT_API_SECRET APP_API_KEY RTVOICE_API_KEY RTVOICE_ADMIN_PASSWORD RTVOICE_SESSION_SECRET; do
  val="$(get_env $key)"
  [[ -n "${val}" ]] || fail ".env 缺少必填项：$key"
  [[ "${val}" != *"changeme"* && "${val}" != *"devsecret"* ]] || fail "$key 是示例值，未修改"
done

docker info > /dev/null 2>&1 || fail "Docker 不可用"
nvidia-smi > /dev/null 2>&1 || fail "nvidia-smi 不可用，GPU 不可访问"

ok "环境检查通过（SERVER_IP=${SERVER_IP}）"

# ─── 1panel-network ───────────────────────────────────────────
log "--- [2/7] 确保 1panel-network 存在 ---"

if ! docker network inspect 1panel-network > /dev/null 2>&1; then
  log "1panel-network 不存在，创建中..."
  docker network create 1panel-network
  ok "1panel-network 已创建"
else
  ok "1panel-network 已就绪"
fi

# ─── 数据目录 ─────────────────────────────────────────────────
log "--- [3/7] 创建数据目录 ---"

for dir in \
  "${DATA_BASE}/keys" \
  "${DATA_BASE}/transcripts" \
  "${DATA_BASE}/cosyvoice-models" \
  "${DATA_BASE}/caddy/data" \
  "${DATA_BASE}/caddy/config" \
  "${DATA_BASE}/prometheus" \
  "${DATA_BASE}/grafana"
do
  mkdir -p "${dir}"
  log "  mkdir -p ${dir}"
done
ok "数据目录创建完成"

# ─── API Keys 初始化 ──────────────────────────────────────────
log "--- [4/7] 初始化 API Keys ---"

KEYS_FILE="${DATA_BASE}/keys/keys.yaml"
if [[ ! -f "${KEYS_FILE}" ]]; then
  RTVOICE_API_KEY="$(get_env RTVOICE_API_KEY)"
  cat > "${KEYS_FILE}" << KEYS_EOF
# RTVoice API Keys — 由 agent-deploy.sh 初始化
# 通过 Admin Console 管理：https://${SERVER_IP}/admin-v2/
keys:
  - key: "${RTVOICE_API_KEY}"
    name: "default"
    scopes: ["stt", "tts", "tokens", "realtime"]
    created_at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
KEYS_EOF
  ok "keys.yaml 已初始化"
else
  ok "keys.yaml 已存在（跳过）"
fi

# ─── 构建镜像 ─────────────────────────────────────────────────
log "--- [5/7] 构建 Docker 镜像 ---"

if [[ "${SKIP_BUILD}" == "1" ]]; then
  log "SKIP_BUILD=1，跳过构建"
else
  log "开始构建（首次约 20-60 分钟，后续有缓存更快）..."
  docker compose \
    --project-directory "${DEPLOY_DIR}" \
    -f "${DEPLOY_DIR}/docker-compose.yml" \
    --env-file "${DEPLOY_DIR}/.env" \
    build --progress=plain 2>&1
  ok "镜像构建完成"
fi

# ─── 启动服务 ─────────────────────────────────────────────────
log "--- [6/7] 启动服务 ---"

# 停止旧服务（如有），保留数据卷
docker compose \
  --project-directory "${DEPLOY_DIR}" \
  -f "${DEPLOY_DIR}/docker-compose.yml" \
  --env-file "${DEPLOY_DIR}/.env" \
  down --remove-orphans 2>/dev/null || true

docker compose \
  --project-directory "${DEPLOY_DIR}" \
  -f "${DEPLOY_DIR}/docker-compose.yml" \
  --env-file "${DEPLOY_DIR}/.env" \
  up -d --no-build

ok "服务已启动"

# ─── 健康检查（等待） ─────────────────────────────────────────
log "--- [7/7] 等待服务健康（最多 5 分钟）---"
log "注意：TTS 首次启动需下载 ~5.6GB 模型，可能需要 10-30 分钟"

WAIT_TIMEOUT=300  # 5 分钟

check_healthy() {
  local container="$1"
  local status
  status=$(docker inspect --format='{{.State.Health.Status}}' "${container}" 2>/dev/null || echo "missing")
  echo "${status}"
}

CORE_CONTAINERS=(rtvoice-livekit rtvoice-token rtvoice-stt rtvoice-realtime rtvoice-caddy)
START_TIME=$(date +%s)

while true; do
  all_healthy=true
  for container in "${CORE_CONTAINERS[@]}"; do
    status=$(check_healthy "${container}")
    if [[ "${status}" != "healthy" ]]; then
      all_healthy=false
      break
    fi
  done

  if [[ "${all_healthy}" == "true" ]]; then
    ok "所有核心服务健康"
    break
  fi

  elapsed=$(( $(date +%s) - START_TIME ))
  if [[ ${elapsed} -ge ${WAIT_TIMEOUT} ]]; then
    log "⚠️  等待超时（${WAIT_TIMEOUT}s），当前状态："
    docker compose \
      --project-directory "${DEPLOY_DIR}" \
      -f "${DEPLOY_DIR}/docker-compose.yml" \
      --env-file "${DEPLOY_DIR}/.env" \
      ps
    log "TTS 可能仍在下载模型，这是正常的。使用：docker logs -f rtvoice-tts 查看进度"
    break
  fi

  log "  等待中（${elapsed}s/${WAIT_TIMEOUT}s）... 当前状态："
  for container in "${CORE_CONTAINERS[@]}"; do
    status=$(check_healthy "${container}")
    log "    ${container}: ${status}"
  done
  sleep 20
done

# ─── 最终状态 ─────────────────────────────────────────────────
echo
echo "=== 部署结果 ==="
docker compose \
  --project-directory "${DEPLOY_DIR}" \
  -f "${DEPLOY_DIR}/docker-compose.yml" \
  --env-file "${DEPLOY_DIR}/.env" \
  ps

echo
echo "=== 关键 URL ==="
echo "Admin Console: https://${SERVER_IP}/admin-v2/"
echo "Grafana 监控:  http://127.0.0.1:13000"
echo
echo "=== 信任 CA 命令（客户端执行）==="
echo "ssh root@${SERVER_IP} 'docker exec rtvoice-caddy cat /data/caddy/pki/authorities/local/root.crt' > rtvoice-ca.crt"
echo
echo "=== 完成 ==="
