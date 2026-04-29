#!/usr/bin/env bash
# dev-down.sh — 停止开发栈
#
# 默认：仅停容器，保留卷与镜像
# 加 --wipe：连同 named volume 一起删除（破坏性，需二次确认）
#
# 见 SECURITY.md

set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)

if [[ "${1:-}" == "--wipe" ]]; then
  echo "⚠️  --wipe 将删除所有 named volume，数据不可恢复。"
  read -r -p "输入 'YES I AM SURE' 继续: " confirm
  if [[ "$confirm" != "YES I AM SURE" ]]; then
    echo "已取消。"
    exit 0
  fi
  "${COMPOSE[@]}" down -v
  echo "✅ 已停止并清空数据卷。"
else
  "${COMPOSE[@]}" down
  echo "✅ 已停止（数据卷保留）。"
  echo "如需清空数据卷：./scripts/dev-down.sh --wipe"
fi
