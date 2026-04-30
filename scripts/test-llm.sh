#!/usr/bin/env bash
# test-llm.sh — autonomous 集成测试 llm-server OpenAI 兼容 API

set -euo pipefail
cd "$(dirname "$0")/.."

if ! docker ps --format '{{.Names}}' | grep -q '^rtvoice-llm$'; then
  echo "❌ rtvoice-llm 未运行" >&2
  exit 1
fi

MODEL=$(grep -E '^LLM_MODEL=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || echo "qwen2.5:1.5b")

echo "▶ 测试 1：/api/tags 含 ${MODEL}"
docker exec rtvoice-llm sh -c "ollama list | awk 'NR>1 {print \$1}' | grep -qx '${MODEL}'" \
  && echo "  ✅ 模型已加载" \
  || (echo "  ❌ 模型未加载"; exit 1)

echo "▶ 测试 2：/v1/chat/completions 流式响应"
docker exec rtvoice-llm python -c "
import json, time, urllib.request

payload = json.dumps({
    'model': '${MODEL}',
    'messages': [
        {'role': 'system', 'content': '简洁中文回答'},
        {'role': 'user', 'content': '你好'}
    ],
    'stream': True,
    'max_tokens': 30
}).encode()
req = urllib.request.Request('http://127.0.0.1:11434/v1/chat/completions',
    data=payload, headers={'Content-Type': 'application/json'}, method='POST')
t0 = time.time()
resp = urllib.request.urlopen(req, timeout=30)
chunks = 0; first = None; reply = ''
for line in resp:
    line = line.decode().strip()
    if not line.startswith('data:'): continue
    line = line[5:].strip()
    if line == '[DONE]': break
    try:
        ev = json.loads(line)
        delta = ev['choices'][0].get('delta', {}).get('content')
        if delta:
            if first is None: first = time.time() - t0
            reply += delta
            chunks += 1
    except Exception: pass
elapsed = time.time() - t0
print(f'  ✅ TTFB={first*1000:.0f}ms total={elapsed*1000:.0f}ms chunks={chunks} reply={reply!r}')
assert reply, 'empty reply'
"

echo "✅ llm-server 集成测试通过"
