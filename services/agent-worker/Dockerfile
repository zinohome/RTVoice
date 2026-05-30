# agent-worker — livekit-rtc + 自管 silero-vad ONNX + 自写状态机
#
# 设计：
# - 不用 silero-vad pip 包（拖 torch + CUDA ~12GB）
# - 直接下载 silero VAD ONNX 文件 (~2MB)，运行期 onnxruntime 加载
# - 镜像 < 500MB

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    wget \
 && rm -rf /var/lib/apt/lists/*

COPY services/agent-worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 下载 silero VAD ONNX 模型（来自官方 github releases）
# 版本 pin v5.1.2；commit hash 验证防被偷换（可选，先简化）
RUN mkdir -p /app/models && \
    wget -q -O /app/models/silero_vad.onnx \
      https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx && \
    ls -la /app/models/

# 卸载 wget（运行期不需要）
RUN apt-get purge -y --auto-remove wget && rm -rf /var/lib/apt/lists/*

COPY services/agent-worker/app ./app

RUN useradd -u 1000 -m appuser && chown -R appuser:appuser /app
USER appuser

# 健康检查：心跳文件 mtime（agent 启动后每 5s touch /tmp/agent-heartbeat）
# 文件 30s 内更新 → healthy；超过 → unhealthy（restart: unless-stopped 兜底）
HEALTHCHECK --interval=15s --timeout=3s --retries=3 --start-period=20s \
  CMD python -c "import os, time, sys; \
    p='/tmp/agent-heartbeat'; \
    sys.exit(0 if os.path.exists(p) and time.time() - os.path.getmtime(p) < 30 else 1)" \
    || exit 1

# AGENT_BACKEND 切换：默认 v05 = main.py（自写 FSM，已验证可工作）
#                     v06 = main_v06.py（livekit-agents framework，experimental）
# v06 默认追加 'start' subcommand（livekit-agents CLI 约定：start/dev/download-files/...）
# 用户可 docker run --entrypoint 跑诊断命令
CMD ["sh", "-c", "if [ \"$AGENT_BACKEND\" = \"v06\" ]; then exec python -m app.main_v06 start; else exec python -m app.main; fi"]
