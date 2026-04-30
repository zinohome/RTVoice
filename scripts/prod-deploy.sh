#!/usr/bin/env bash
# prod-deploy.sh — RTVoice 生产部署（落地 SECURITY.md §4 四阶段协议）
#
# 用途：在 RTX 3060 GPU 服务器上部署 / 升级 RTVoice
# 风险：操作生产服务，每步明确影响范围 + 用户二次确认
# 回滚：每步声明回滚命令；任意步骤可中断
#
# 阶段：
#   1. 只读探查（不改任何状态）
#   2. 备份现有数据卷与配置
#   3. 拉/构建镜像（不重启服务）
#   4. 单服务渐进部署 + healthcheck 等待
#
# 用法：
#   ./scripts/prod-deploy.sh             # 全流程交互
#   ./scripts/prod-deploy.sh --inspect   # 仅阶段 1（只读）
#   ./scripts/prod-deploy.sh --backup    # 仅阶段 2
#   ./scripts/prod-deploy.sh --apply     # 阶段 3+4（需用户已 inspect+backup）

set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
SERVICES=(livekit-server token-server stt-server llm-server tts-server agent-worker)

confirm() {
  local msg="$1"
  read -r -p "${msg} [y/N]: " ans
  [[ "$ans" =~ ^[Yy]$ ]]
}

require_env_prod() {
  if [[ ! -f .env ]]; then
    echo "❌ .env 不存在；先 cp .env.example .env 并填入 prod 值" >&2
    exit 1
  fi
  for k in LIVEKIT_API_KEY LIVEKIT_API_SECRET APP_API_KEY BIND_HOST LIVEKIT_PUBLIC_URL; do
    val=$(grep -E "^${k}=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
    if [[ -z "$val" ]]; then
      echo "❌ .env 缺 ${k}（生产必填）" >&2
      exit 1
    fi
  done
  # 安全检查：不允许 dev 默认值上 prod
  for k in APP_API_KEY LIVEKIT_API_SECRET; do
    val=$(grep -E "^${k}=" .env | head -1 | cut -d= -f2-)
    if [[ "$val" == *"changeme"* || "$val" == *"devsecret"* ]]; then
      echo "❌ .env 中 ${k} 仍是默认弱值。生产必须重新生成。" >&2
      echo "   生成：python3 -c 'import secrets; print(secrets.token_urlsafe(32))'" >&2
      exit 1
    fi
  done
}

phase1_inspect() {
  echo "════════════════════════════════════════════"
  echo "  阶段 1：只读探查（不改任何状态）"
  echo "════════════════════════════════════════════"
  echo
  echo "▶ NVIDIA 驱动与 GPU"
  nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader || \
    echo "  ⚠️  nvidia-smi 不可用，prod GPU 服务无法启动"
  echo
  echo "▶ Docker 信息"
  docker --version
  docker info 2>/dev/null | grep -iE "runtime|nvidia" | head -5 || true
  echo
  echo "▶ 本机已有 RTVoice 容器"
  docker ps -a --filter "name=rtvoice-" --format "table {{.Names}}\t{{.Image}}\t{{.Status}}" || true
  echo
  echo "▶ 数据卷"
  docker volume ls --filter "name=rtvoice_" --format "table {{.Name}}\t{{.Driver}}"
  echo
  echo "▶ 磁盘空间"
  df -h / | tail -1
  echo
  echo "▶ 内存"
  free -h | head -2
  echo
  echo "▶ .env 关键键存在性（不显示值）"
  for k in LIVEKIT_API_KEY LIVEKIT_API_SECRET APP_API_KEY BIND_HOST LIVEKIT_PUBLIC_URL LLM_MODEL_PROD; do
    if grep -qE "^${k}=" .env 2>/dev/null; then echo "  ✅ ${k}"; else echo "  ❌ ${k}"; fi
  done
  echo
  echo "▶ docker compose config 校验"
  "${COMPOSE[@]}" --profile prod config --quiet && echo "  ✅ compose 文件语法正确"
}

phase2_backup() {
  echo "════════════════════════════════════════════"
  echo "  阶段 2：备份"
  echo "════════════════════════════════════════════"
  ts=$(date +%F-%H%M)
  mkdir -p "backups/${ts}"
  echo
  echo "▶ 备份 .env（去敏感后）"
  grep -vE 'SECRET|KEY' .env > "backups/${ts}/env-redacted.txt" 2>/dev/null || true
  echo "▶ 备份 compose 文件"
  cp docker-compose.yml docker-compose.prod.yml "backups/${ts}/" 2>/dev/null || true
  cp -r livekit/ "backups/${ts}/livekit/" 2>/dev/null || true
  echo "▶ 备份数据卷"
  ./scripts/backup-volumes.sh "backups/${ts}/volumes"
  echo
  echo "✅ 备份完成：backups/${ts}/"
}

phase3_pull_build() {
  echo "════════════════════════════════════════════"
  echo "  阶段 3：拉镜像 + 构建（不重启服务）"
  echo "════════════════════════════════════════════"
  echo
  echo "▶ pull 远端镜像（livekit/vllm-openai 等）"
  "${COMPOSE[@]}" --profile prod pull --ignore-buildable
  echo
  echo "▶ build 自建镜像（rtvoice/* 含 GPU 变体）"
  "${COMPOSE[@]}" --profile prod build
  echo
  echo "✅ 镜像就绪。"
}

phase4_apply() {
  echo "════════════════════════════════════════════"
  echo "  阶段 4：单服务渐进部署"
  echo "════════════════════════════════════════════"
  echo
  echo "  顺序：livekit → stt → llm → tts → token → agent"
  echo "  每个服务等 healthcheck 转 healthy 再继续"
  echo
  if ! confirm "继续？"; then return 0; fi

  for svc in livekit-server stt-server llm-server tts-server token-server agent-worker; do
    echo
    echo "▶ 部署 ${svc}（影响：仅此服务；可逆性：可，回滚命令见下）"
    echo "  回滚：docker compose ... up -d --no-deps <prev-tag-svc>"
    if ! confirm "  应用 ${svc}？"; then echo "  跳过"; continue; fi
    "${COMPOSE[@]}" --profile prod up -d --no-deps "${svc}"

    # 等 healthcheck（最多 10 分钟，LLM 首次拉模型可能慢）
    echo "  等待 ${svc} 健康..."
    for i in $(seq 1 120); do
      status=$(docker inspect --format='{{.State.Health.Status}}' "rtvoice-${svc##*-server}" 2>/dev/null \
              || docker inspect --format='{{.State.Health.Status}}' "rtvoice-${svc%-worker}" 2>/dev/null \
              || echo "?")
      if [[ "$status" == "healthy" || "$status" == "<no value>" ]]; then
        echo "  ✅ ${svc} 健康"
        break
      fi
      sleep 5
    done
  done

  echo
  echo "✅ 全部服务部署完成。"
  "${COMPOSE[@]}" --profile prod ps
}

# ---------------- main ----------------
case "${1:-}" in
  --inspect)
    phase1_inspect
    ;;
  --backup)
    require_env_prod
    phase1_inspect
    confirm "继续阶段 2 备份？" || exit 0
    phase2_backup
    ;;
  --apply)
    require_env_prod
    phase3_pull_build
    confirm "继续阶段 4 渐进部署？" || exit 0
    phase4_apply
    ;;
  --help|-h|"")
    echo "用法："
    echo "  $0 --inspect    阶段 1（只读探查）"
    echo "  $0 --backup     阶段 1+2"
    echo "  $0 --apply      阶段 3+4（已 inspect/backup 后）"
    echo
    echo "推荐流程："
    echo "  $0 --inspect"
    echo "  # 检查输出，确认 GPU/驱动/磁盘 OK"
    echo "  $0 --backup"
    echo "  $0 --apply"
    ;;
  *)
    echo "未知参数 $1；查看 --help" >&2
    exit 1
    ;;
esac
