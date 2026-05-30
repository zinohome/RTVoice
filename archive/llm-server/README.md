# llm-server

**职责**：本地 LLM 服务，提供 OpenAI 兼容 streaming API（`POST /v1/chat/completions`）。

**状态**：✅ v0.4（ollama + Qwen2.5-1.5B Q4 CPU）

## 技术栈

| 组件 | 选型 |
|---|---|
| 推理引擎 | ollama 0.22.0（CPU 优化，自动量化） |
| 模型 | `qwen2.5:1.5b` (Q4_K_M, ~1GB) |
| API | OpenAI 兼容 `/v1/chat/completions` |

## 选型理由（ENGINES.md §4）

- dev：ollama 是面向"本地开发者"的 wrapper，开箱即用 + Q4 默认 + 模型自动管理
- prod v0.5：会换 vLLM + Qwen2.5-3B GPU，**相同 OpenAI API 协议**，客户端零改动

## 接口

### Chat completions（流式）
```
POST http://llm-server:11434/v1/chat/completions
Content-Type: application/json

{
  "model": "qwen2.5:1.5b",
  "messages": [
    {"role": "system", "content": "你是语音助手 RTVoice..."},
    {"role": "user", "content": "你好"}
  ],
  "stream": true,
  "max_tokens": 200
}
```

返回 OpenAI 标准 SSE：
```
data: {"choices":[{"delta":{"content":"你"}}]}
data: {"choices":[{"delta":{"content":"好"}}]}
...
data: [DONE]
```

### 模型列表
```
GET /api/tags
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_MODEL` | `qwen2.5:1.5b` | 启动时自动 pull 的模型 ID |
| `OLLAMA_HOST` | `0.0.0.0:11434` | ollama 监听地址 |
| `OLLAMA_KEEP_ALIVE` | `5m` | 模型在内存保留时长（控制冷热切换） |

## 卷

| 名称 | 路径 | 用途 |
|---|---|---|
| `rtvoice_ollama_models` | `/root/.ollama` | 模型权重 + 元数据；首次 pull 后持久 |

## 启动流程

`entrypoint.sh`：
1. 后台启动 `ollama serve`
2. 轮询 `ollama list` 至成功（最多 60s）
3. 检查 `LLM_MODEL` 是否已存在
4. 不存在则 `ollama pull <model>`（首次 1-5 分钟）
5. wait ollama serve 进程

**首次启动慢**：模型 pull 走 ollama 官方 CDN，~1GB。后续 restart 秒起（卷里有了）。

## v0.4 已知限制

- ⚠️ **CPU 推理**：1.5B 在 4 核 ~ 20 tok/s，首 token ~500ms，实测延迟略高于 ENGINES.md 预算
- ⚠️ **无 GPU 加速**：dev 必然，prod v0.5 切 vLLM + Qwen2.5-3B GPU
- ⚠️ **无鉴权**：仅 docker network 内可达；公网暴露需加层
- ⚠️ **模型 pull 失败时 serve 继续运行**：客户端会收到 model not found；这是 fail-loud 选择

## 演进路线

| 版本 | 变化 |
|---|---|
| v0.4 | ollama + Qwen2.5-1.5B CPU（当前） |
| v0.5 | vLLM + Qwen2.5-3B GPU；docker-compose.prod.yml 切镜像 |
| v0.6 | 加上下文记忆（multi-turn chat history） |
