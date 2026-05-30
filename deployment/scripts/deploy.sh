#!/usr/bin/env bash
# deploy.sh — RTVoice 人类版一键部署脚本
#
# 目标环境：已安装 1Panel、Docker（含 NVIDIA container toolkit）的 GPU 服务器
# 数据目录：/data/RTVoice/
# 网络：1panel-network（1Panel 预创建）
#
# 用法（在服务器上运行）：
#   cd /data/RTVoice/deployment
#   cp .env.example .env && nano .env    # 修改 SERVER_IP 和各密钥
#   bash scripts/deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"

cd "${DEPLOY_DIR}"

# ─── 颜色输出 ────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✅ $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠️  $*${NC}"; }
fail() { echo -e "${RED}  ❌ $*${NC}" >&2; exit 1; }
step() { echo; echo -e "${YELLOW}▶ $*${NC}"; }

confirm() {
  local msg="$1"
  read -r -p "  ${msg} [y/N]: " ans
  [[ "$ans" =~ ^[Yy]$ ]]
}

# ─── Step 0: 前置检查 ─────────────────────────────────────────
step "Step 0: 前置环境检查"

# .env 存在性
[[ -f .env ]] || fail ".env 不存在。请先：cp .env.example .env 并填写所有 [必改] 项"

# 加载 .env 中的 SERVER_IP 做基础校验
source_env() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'"; }
SERVER_IP="$(source_env SERVER_IP)"
[[ -n "$SERVER_IP" ]] || fail ".env 中 SERVER_IP 未设置"
[[ "$SERVER_IP" != "192.168.X.X" ]] || fail "SERVER_IP 仍是示例值，请改为真实服务器 IP"

# 必填密钥检查
for key in LIVEKIT_API_KEY LIVEKIT_API_SECRET APP_API_KEY RTVOICE_API_KEY RTVOICE_ADMIN_PASSWORD RTVOICE_SESSION_SECRET; do
  val="$(source_env $key)"
  [[ -n "$val" ]] || fail ".env 缺少 $key（必填）"
  [[ "$val" != *"changeme"* && "$val" != *"devsecret"* ]] || fail "$key 仍是示例默认值，请重新生成"
done

# Docker 可用性
docker info > /dev/null 2>&1 || fail "Docker 不可用或当前用户没有权限，尝试：sudo usermod -aG docker \$USER"

# NVIDIA GPU
if ! nvidia-smi > /dev/null 2>&1; then
  warn "nvidia-smi 不可用，TTS GPU 服务将无法启动。继续？"
  confirm "跳过 GPU 检查继续部署" || exit 0
else
  GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1)
  ok "GPU 就绪：${GPU_INFO}"
fi

# NVIDIA Container Toolkit
if ! docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi > /dev/null 2>&1; then
  fail "NVIDIA Container Toolkit 未安装或未配置。\n安装文档：https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
fi
ok "NVIDIA Container Toolkit 正常"

# 1panel-network 检查（1Panel 应已创建）
if ! docker network inspect 1panel-network > /dev/null 2>&1; then
  warn "1panel-network 不存在，尝试创建..."
  docker network create 1panel-network || fail "创建 1panel-network 失败，请确认 1Panel 已安装并启动"
fi
ok "1panel-network 就绪"

# ollama 检查（LLM 用宿主 ollama）
LLM_MODEL="$(source_env LLM_MODEL_PROD)"
LLM_MODEL="${LLM_MODEL:-qwen2.5:7b}"
if command -v ollama > /dev/null 2>&1; then
  if ollama list 2>/dev/null | grep -q "${LLM_MODEL%%:*}"; then
    ok "ollama 模型 ${LLM_MODEL} 已就绪"
  else
    warn "ollama 模型 ${LLM_MODEL} 未找到。部署后请运行：ollama pull ${LLM_MODEL}"
  fi
else
  warn "ollama 未检测到，LLM 功能需要宿主安装 ollama 并拉取 ${LLM_MODEL}"
fi

ok "前置检查通过（SERVER_IP=${SERVER_IP}）"

# ─── Step 1: 创建数据目录 ─────────────────────────────────────
step "Step 1: 创建数据目录 /data/RTVoice/"

DATA_DIRS=(
  /data/RTVoice/keys
  /data/RTVoice/transcripts
  /data/RTVoice/cosyvoice-models
  /data/RTVoice/caddy/data
  /data/RTVoice/caddy/config
  /data/RTVoice/prometheus
  /data/RTVoice/grafana
)

for dir in "${DATA_DIRS[@]}"; do
  mkdir -p "$dir"
  echo "  mkdir -p $dir"
done
ok "数据目录已创建"

# ─── Step 2: 初始化 API Keys ──────────────────────────────────
step "Step 2: 初始化 API Keys 配置"

KEYS_FILE="/data/RTVoice/keys/keys.yaml"
if [[ ! -f "$KEYS_FILE" ]]; then
  RTVOICE_API_KEY="$(source_env RTVOICE_API_KEY)"
  cat > "$KEYS_FILE" << KEYS_EOF
# RTVoice API Keys（由 deploy.sh 初始化）
# 使用 Admin Console 管理 key：https://${SERVER_IP}/admin/
keys:
  - key: "${RTVOICE_API_KEY}"
    name: "default"
    scopes: ["stt", "tts", "tokens", "realtime"]
    created_at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
KEYS_EOF
  ok "已创建初始 API Key 配置：${KEYS_FILE}"
else
  ok "API Key 配置已存在（不覆盖）"
fi

# ─── Step 3: 构建镜像 ─────────────────────────────────────────
step "Step 3: 构建 Docker 镜像（首次约需 20-60 分钟，后续有缓存更快）"

echo "  工作目录：${REPO_ROOT}"
echo "  Compose 文件：${DEPLOY_DIR}/docker-compose.yml"
echo

if ! confirm "开始构建镜像？（时间较长）"; then
  echo "  已跳过构建。如镜像已存在可直接运行 Step 4。"
else
  docker compose \
    --project-directory "${DEPLOY_DIR}" \
    -f "${DEPLOY_DIR}/docker-compose.yml" \
    --env-file "${DEPLOY_DIR}/.env" \
    build --progress=plain
  ok "镜像构建完成"
fi

# ─── Step 4: 启动服务 ─────────────────────────────────────────
step "Step 4: 启动所有服务"

if ! confirm "启动 RTVoice？"; then exit 0; fi

docker compose \
  --project-directory "${DEPLOY_DIR}" \
  -f "${DEPLOY_DIR}/docker-compose.yml" \
  --env-file "${DEPLOY_DIR}/.env" \
  up -d

echo
echo "  等待服务启动（最多 5 分钟，TTS 首次启动需下载模型会更慢）..."
sleep 10

# ─── Step 5: 健康检查 ─────────────────────────────────────────
step "Step 5: 服务状态"

docker compose \
  --project-directory "${DEPLOY_DIR}" \
  -f "${DEPLOY_DIR}/docker-compose.yml" \
  --env-file "${DEPLOY_DIR}/.env" \
  ps

echo
echo "══════════════════════════════════════════════════════"
echo "  RTVoice 部署完成！"
echo "══════════════════════════════════════════════════════"
echo
echo "  访问地址："
echo "  Admin Console：https://${SERVER_IP}/admin/"
echo "  Grafana 监控：http://127.0.0.1:13000  (admin/admin 首次改密)"
echo
echo "  ⚠️  首次访问 HTTPS 需信任 Caddy 自签 CA："
echo "  docker exec rtvoice-caddy cat /data/caddy/pki/authorities/local/root.crt > rtvoice-ca.crt"
echo "  # 然后将 rtvoice-ca.crt 导入客户端系统信任链（详见 README.md）"
echo
echo "  查看日志：docker compose -f ${DEPLOY_DIR}/docker-compose.yml logs -f"
echo "  停止服务：docker compose -f ${DEPLOY_DIR}/docker-compose.yml down"
echo
echo "  TTS 首次启动需下载 ~5.6GB 模型，观察进度："
echo "  docker logs -f rtvoice-tts"
