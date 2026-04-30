#!/usr/bin/env bash
# backup-volumes.sh — 备份所有 RTVoice named volume 为 tar.gz
#
# 用途：升级/重启前快照所有持久数据
# 风险：仅只读复制，不修改卷
# 回滚：./scripts/restore-volume.sh <vol> <tar>（v0.6 待写）
#
# 用法：
#   ./scripts/backup-volumes.sh [output_dir]
#   默认 output_dir = backups/<timestamp>

set -euo pipefail

cd "$(dirname "$0")/.."

OUT_DIR="${1:-backups/$(date +%F-%H%M)}"
mkdir -p "$OUT_DIR"

# 找所有 rtvoice_ 前缀的卷
mapfile -t VOLS < <(docker volume ls --filter "name=rtvoice_" --format '{{.Name}}')

if [[ ${#VOLS[@]} -eq 0 ]]; then
  echo "未发现任何 rtvoice_ 前缀的卷"
  exit 0
fi

echo "▶ 备份 ${#VOLS[@]} 个卷到 $OUT_DIR/"
for vol in "${VOLS[@]}"; do
  out="$OUT_DIR/${vol}.tar.gz"
  echo "  $vol → $out"
  # 用 alpine 临时容器打 tar；卷只读挂载，不改原卷
  docker run --rm \
    -v "${vol}:/data:ro" \
    -v "$(pwd)/${OUT_DIR}:/backup" \
    alpine \
    sh -c "cd /data && tar czf /backup/${vol}.tar.gz . 2>/dev/null"
  size=$(du -h "$out" | cut -f1)
  echo "    → $size"
done

echo
echo "✅ 完成：$OUT_DIR/"
ls -la "$OUT_DIR/"
