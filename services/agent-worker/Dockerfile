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

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 下载 silero VAD ONNX 模型（来自官方 github releases）
# 版本 pin v5.1.2；commit hash 验证防被偷换（可选，先简化）
RUN mkdir -p /app/models && \
    wget -q -O /app/models/silero_vad.onnx \
      https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx && \
    ls -la /app/models/

# 卸载 wget（运行期不需要）
RUN apt-get purge -y --auto-remove wget && rm -rf /var/lib/apt/lists/*

COPY app ./app

RUN useradd -u 1000 -m appuser && chown -R appuser:appuser /app
USER appuser

# 健康检查：v0.3+ 改为读 /tmp/agent-heartbeat 文件 mtime（agent 加入 room 后定期 touch）。
# v0.2 暂时禁用——agent 退出会被 restart: unless-stopped 兜底。
# （python:3.11-slim 不带 procps，原 pgrep 方案永远 unhealthy）
HEALTHCHECK NONE

CMD ["python", "-m", "app.main"]
