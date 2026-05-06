#!/bin/bash
# Fun-CosyVoice 3 入口：首次启动下载模型，后续秒起
#
# 关键模型文件检查（v3 与 v2 文件名差异）：
#   v3 必备：llm.pt / flow.pt / hift.pt / cosyvoice3.yaml / speech_tokenizer_v3.onnx
#   v3 可选：llm.rl.pt（RL-tuned 变体，~2GB；不下省一半空间）
#
# Modelscope ID 切换由 MODEL_ID 环境变量控制（默认 v3 官方仓）
# HuggingFace 镜像走 HF_ENDPOINT；中国用 hf-mirror.com
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-/opt/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B-2512}"
MODEL_ID="${MODEL_ID:-FunAudioLLM/Fun-CosyVoice3-0.5B-2512}"

# 检查 model 是否完整：v3 必须的核心 5 个文件
need_download=false
for f in llm.pt flow.pt hift.pt cosyvoice3.yaml speech_tokenizer_v3.onnx; do
    if [ ! -f "$MODEL_DIR/$f" ]; then
        echo "[entrypoint] 缺失文件：$f"
        need_download=true
        break
    fi
done

if $need_download; then
    echo "[entrypoint] 首次启动：下载 $MODEL_ID 到 $MODEL_DIR (~5.6GB)..."
    mkdir -p "$(dirname "$MODEL_DIR")"
    # 默认用 modelscope（国内更快）；HF_ENDPOINT 走 hf-mirror 时也可改 huggingface_hub
    python -c "
from modelscope import snapshot_download
# allow_patterns 排除 llm.rl.pt 省 2GB；要 RL 变体可设 SKIP_RL_MODEL=0
import os
allow = None if os.environ.get('SKIP_RL_MODEL', '1') == '0' else [
    '*.yaml', '*.txt', '*.json', '*.md',
    'llm.pt', 'flow.pt', 'hift.pt',
    'flow.decoder.estimator.fp32.onnx',
    'speech_tokenizer_v3.onnx', 'speech_tokenizer_v3.batch.onnx',
    'campplus.onnx',
    'CosyVoice-BlankEN/*',
]
snapshot_download('$MODEL_ID', local_dir='$MODEL_DIR', allow_patterns=allow)
"
    echo "[entrypoint] 模型下载完成"
else
    echo "[entrypoint] 模型已就绪：$MODEL_DIR"
    ls -lh "$MODEL_DIR" | head -10
fi

# 启动应用（CMD 传入的命令）
exec "$@"
