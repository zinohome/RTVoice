# stt-server — sherpa-onnx 中文流式 ASR (CPU)
#
# v0.3 dev：sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20
#
# 模型选择历史：
#   - ENGINES.md §2 原计划用 Paraformer streaming
#   - 实测发现 Paraformer streaming 的 HF 仓库匿名下载受限（401）
#   - Zipformer streaming 是 ENGINES.md 同档备选（中文 SOTA + CPU 友好），
#     公开可下载，与 Paraformer 同协议接入，零改动切换
#   - v0.5 prod 阶段评估实测 CER 后再决定是否切回 Paraformer

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

COPY services/common /app/common
COPY services/stt-server/requirements.txt /app/
RUN pip install --upgrade pip && \
    pip install -r /app/requirements.txt && \
    pip install --no-cache-dir /app/common

# Zipformer streaming bilingual zh-en (int8 量化版，~120MB 总体)
ARG MODEL_NAME=sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20
ARG HF_REPO=csukuangfj/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20

RUN mkdir -p /app/models/${MODEL_NAME} && cd /app/models/${MODEL_NAME} && \
    base="https://huggingface.co/${HF_REPO}/resolve/main" && \
    for f in \
        "encoder-epoch-99-avg-1.int8.onnx" \
        "decoder-epoch-99-avg-1.int8.onnx" \
        "joiner-epoch-99-avg-1.int8.onnx" \
        "tokens.txt" \
        "test_wavs/0.wav" \
        "test_wavs/1.wav" \
    ; do \
        mkdir -p "$(dirname $f)" && \
        wget -q -O "$f" "${base}/${f}" || (echo "FAILED: $f"; exit 1); \
    done && \
    ls -la encoder*.onnx decoder*.onnx joiner*.onnx tokens.txt

# 卸载下载工具
RUN apt-get purge -y --auto-remove wget && rm -rf /var/lib/apt/lists/*

COPY services/stt-server/app /app/app
ENV PYTHONPATH=/app/common:/app

RUN useradd -u 1000 -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 9090

HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=30s \
  CMD python -c "import urllib.request,json,sys; r=json.load(urllib.request.urlopen('http://127.0.0.1:9090/health',timeout=2)); sys.exit(0 if r.get('status')=='ok' else 1)" \
    || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9090"]
