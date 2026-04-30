# llm-server — ollama 的本地 LLM 服务（OpenAI 兼容 API）
#
# 选型理由 ENGINES.md §4：
#   - dev：ollama + Qwen2.5-1.5B (Q4) CPU，4 核机器 20+ tok/s
#   - prod v0.5：换 vLLM + Qwen2.5-3B (GPU)，相同 OpenAI API 协议无缝切
#
# 设计：
#   - FROM ollama/ollama 官方镜像 + 自定义 entrypoint
#   - entrypoint 启动 ollama serve 后自动 pull 指定模型（首次拉取持久到 volume）
#   - 后续 restart 直接复用 volume 中的模型（秒级启动）

ARG OLLAMA_VERSION=0.22.0
FROM ollama/ollama:${OLLAMA_VERSION}

# 安装 curl 用于 healthcheck（official 镜像可能没有）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Ollama 默认 11434
EXPOSE 11434

# 模型默认值（可被 docker-compose 环境变量覆盖）
ENV LLM_MODEL=qwen2.5:1.5b

# /entrypoint.sh 启动 ollama serve + 自动 pull 模型
ENTRYPOINT ["/entrypoint.sh"]
