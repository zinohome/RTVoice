# Token Server — FastAPI 容器化
# 设计：
#   - python:3.11-slim 基础镜像（小且官方）
#   - 非 root 运行
#   - 多阶段不必要（无编译产物），单阶段保持简单
#   - SP6 T12: build context 改 monorepo root，引入 services/common (rtvoice_auth)

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY services/common /app/common
COPY services/token-server/requirements.txt /app/
RUN pip install --upgrade pip && \
    pip install -r /app/requirements.txt && \
    pip install --no-cache-dir /app/common

# 复制应用代码与静态资源
COPY services/token-server/app /app/app
COPY services/token-server/static /app/static
ENV PYTHONPATH=/app/common:/app

# 创建非 root 用户
RUN useradd -u 1000 -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 健康检查由 docker-compose.yml 定义（python urllib），此处不重复

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
