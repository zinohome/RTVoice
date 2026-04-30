# tts-server — Kokoro 82M ONNX (CPU)
#
# v0.5 dev：Kokoro v1.0 ONNX，~325MB 模型，CPU 跑 1× realtime+
# v0.5+ prod：会切 CosyVoice 2-0.5B GPU（同协议同代码，docker-compose.prod.yml 切镜像）
#
# 选型理由 ENGINES.md §3：CosyVoice 2 CPU 5-10× realtime，破坏流式承诺；
# Kokoro 82M CPU 1×+ realtime，中文质量"过得去"，dev 阶段够用

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# espeak-ng-data：phonemizer 需要的语言数据（包括中文 cmn）
# espeakng-loader 包自带 libespeak-ng.so，但 data 目录需要系统提供
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    wget \
    espeak-ng-data \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 下载 Kokoro 模型 + 音色 embedding
ARG KOKORO_VERSION=v1.0
RUN mkdir -p /app/models && cd /app/models && \
    base="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-${KOKORO_VERSION}" && \
    wget -q -O "kokoro-${KOKORO_VERSION}.onnx" "${base}/kokoro-${KOKORO_VERSION}.onnx" && \
    wget -q -O "voices-${KOKORO_VERSION}.bin"  "${base}/voices-${KOKORO_VERSION}.bin" && \
    ls -la

# 卸载下载工具
RUN apt-get purge -y --auto-remove wget && rm -rf /var/lib/apt/lists/*

COPY app ./app

RUN useradd -u 1000 -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 9880

HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=60s \
  CMD python -c "import urllib.request,json,sys; r=json.load(urllib.request.urlopen('http://127.0.0.1:9880/health',timeout=3)); sys.exit(0 if r.get('status')=='ok' else 1)" \
    || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9880"]
