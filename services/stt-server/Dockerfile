# stt-server — sherpa-onnx 中文 ASR (CPU)
#
# v0.20：SenseVoice-Small（FunAudioLLM，非自回归 offline 模型）
#
# 模型选择历史：
#   - v0.3 dev：Paraformer streaming（HF 匿名下载 401，放弃）
#   - v0.3-0.19：streaming Zipformer bilingual zh-en 2023-02-20（transducer+greedy）
#     —— 自回归+贪心解码会掉进 token 重复循环（啦啦啦/妈妈妈幻觉）
#   - v0.20：切 SenseVoice-Small —— 非自回归一次性出整段，架构性免疫重复幻觉，
#     中文 CER 更低，int8 权重 <300MB、CPU 即可、显存零占用

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

# SenseVoice-Small（int8 量化版，model.int8.onnx ~230MB + tokens.txt）
ARG MODEL_NAME=sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17
ARG HF_REPO=csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17

COPY scripts/download_model.sh /usr/local/bin/download_model.sh
RUN chmod +x /usr/local/bin/download_model.sh

RUN mkdir -p /app/models/${MODEL_NAME} && cd /app/models/${MODEL_NAME} && \
    base="https://huggingface.co/${HF_REPO}/resolve/main" && \
    /usr/local/bin/download_model.sh "${base}/model.int8.onnx" "model.int8.onnx" 104857600 && \
    /usr/local/bin/download_model.sh "${base}/model.onnx" "model.onnx" 1073741824 && \
    /usr/local/bin/download_model.sh "${base}/tokens.txt" "tokens.txt" 1024

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
