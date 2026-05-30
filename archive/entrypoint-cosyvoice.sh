#!/bin/bash
# CosyVoice 2 入口：首次启动下载模型（~5GB），后续秒起
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-/opt/CosyVoice/pretrained_models/CosyVoice2-0.5B}"
MODEL_ID="iic/CosyVoice2-0.5B"

# 检查 model 是否完整（关键文件 llm.pt + flow.pt + hift.pt）
need_download=false
for f in llm.pt flow.pt hift.pt; do
    if [ ! -f "$MODEL_DIR/$f" ]; then
        need_download=true
        break
    fi
done

if $need_download; then
    echo "[entrypoint] 首次启动：下载 $MODEL_ID 到 $MODEL_DIR (~5GB)..."
    mkdir -p "$(dirname "$MODEL_DIR")"
    python -c "
from modelscope import snapshot_download
snapshot_download('$MODEL_ID', local_dir='$MODEL_DIR')
"
    echo "[entrypoint] 模型下载完成"
else
    echo "[entrypoint] 模型已就绪：$MODEL_DIR"
    ls -lh "$MODEL_DIR" | head -8
fi

# 启动应用（CMD 传入的命令）
exec "$@"
