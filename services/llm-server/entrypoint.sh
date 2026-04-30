#!/bin/bash
# llm-server entrypoint
#
# 1. 后台启动 ollama serve
# 2. 等服务就绪
# 3. 检查模型是否已存在；不存在则 pull
# 4. 转入前台等 ollama serve（让容器活着）
#
# 失败语义：模型 pull 失败也不退出，让 ollama serve 继续——
# 客户端会收到模型不存在的明确错误，比静默崩溃更易诊断。

set -uo pipefail

MODEL="${LLM_MODEL:-qwen2.5:1.5b}"
WAIT_MAX=60   # 等 server 就绪最多 60s

echo "[llm-server] 启动 ollama serve..."
/bin/ollama serve &
SERVE_PID=$!

# 等服务就绪（用 ollama list 探活，要求服务连接成功）
echo "[llm-server] 等 ollama 服务就绪..."
ready=0
for i in $(seq 1 $WAIT_MAX); do
    if /bin/ollama list >/dev/null 2>&1; then
        ready=1
        echo "[llm-server] ollama 就绪（${i}s）"
        break
    fi
    sleep 1
done

if [[ $ready -eq 0 ]]; then
    echo "[llm-server] ❌ ollama 启动超时（${WAIT_MAX}s）" >&2
    exit 1
fi

# 检查模型；不存在则拉取
if /bin/ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$MODEL"; then
    echo "[llm-server] ✅ 模型已存在: $MODEL"
else
    echo "[llm-server] 拉取模型 $MODEL（首次可能 1-5 分钟）..."
    if /bin/ollama pull "$MODEL"; then
        echo "[llm-server] ✅ 模型拉取完成: $MODEL"
    else
        echo "[llm-server] ⚠️  模型拉取失败，serve 继续运行；客户端将收到 'model not found' 错误" >&2
    fi
fi

echo "[llm-server] ready (model=$MODEL, pid=$SERVE_PID)"
wait $SERVE_PID
