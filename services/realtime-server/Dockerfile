FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/common /app/common
COPY services/rtvoice-admin /app/rtvoice-admin
COPY services/realtime-server/requirements.txt /app/
RUN pip install --upgrade pip && \
    pip install -r /app/requirements.txt && \
    pip install --no-cache-dir /app/common && \
    pip install --no-cache-dir /app/rtvoice-admin
COPY services/realtime-server/app /app/app
# SP9 T5 — prod web demo 真实来源是 clients/web/（4-tab demo）
# 之前 services/realtime-server/static/ 是 SP3 旧 4KB Realtime 测试页，已迁
COPY clients/web /app/static
ENV PYTHONPATH=/app/common:/app

RUN useradd -u 1000 -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 9000

HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9000/health',timeout=2).status==200 else 1)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
