#!/usr/bin/env bash
# dev-up.sh — 启动开发栈（CPU only / mock 引擎 / 仅本机访问）
#
# 用途：一键起 livekit-server + token-server，浏览器可加入房间测试
# 风险：无（仅本机 docker 操作，端口绑 127.0.0.1）
# 回滚：./scripts/dev-down.sh
#
# 见 SECURITY.md / DEPLOY.md

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "❌ .env 不存在。请先：cp .env.example .env 并填写 LIVEKIT_API_KEY/SECRET" >&2
  exit 1
fi

# 简单校验关键密钥长度（防止使用空值或默认值）
get_env() {
  grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'"
}
SECRET=$(get_env LIVEKIT_API_SECRET)
APIKEY=$(get_env APP_API_KEY)
if [[ ${#SECRET} -lt 16 ]]; then
  echo "❌ .env 中 LIVEKIT_API_SECRET 长度不足 16 字符。请重新生成。" >&2
  exit 1
fi
if [[ ${#APIKEY} -lt 32 ]]; then
  echo "❌ .env 中 APP_API_KEY 长度不足 32 字符。生成：" >&2
  echo "   python3 -c 'import secrets; print(secrets.token_urlsafe(32))'" >&2
  exit 1
fi
if [[ "$APIKEY" == "changemechangemechangemechangeme32" ]]; then
  echo "❌ APP_API_KEY 仍是 .env.example 默认值。请生成真实随机值。" >&2
  exit 1
fi

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)

echo "▶ 构建镜像..."
"${COMPOSE[@]}" build

echo "▶ 启动服务（profile=dev）..."
"${COMPOSE[@]}" --profile dev up -d

echo "▶ 等待健康检查（最多 30s）..."
for i in $(seq 1 15); do
  if "${COMPOSE[@]}" ps --format json 2>/dev/null | grep -q '"Health":"healthy"'; then
    break
  fi
  sleep 2
done

echo
"${COMPOSE[@]}" ps
echo
echo "✅ 启动完成。访问："
BIND_HOST=$(grep -E '^BIND_HOST=' .env | head -1 | cut -d= -f2- || echo "127.0.0.1")
PORT=$(grep -E '^TOKEN_SERVER_PORT=' .env | head -1 | cut -d= -f2- || echo "8000")
echo "  浏览器测试页:  http://${BIND_HOST}:${PORT}/"
echo "  健康检查:      curl http://${BIND_HOST}:${PORT}/health"
echo
echo "查看日志:  ${COMPOSE[*]} logs -f"
echo "停止:      ./scripts/dev-down.sh"
