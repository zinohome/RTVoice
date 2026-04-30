#!/usr/bin/env bash
# test-tts.sh — autonomous 集成测试 tts-server HTTP 流式

set -euo pipefail
cd "$(dirname "$0")/.."

if ! docker ps --format '{{.Names}}' | grep -q '^rtvoice-tts$'; then
  echo "❌ rtvoice-tts 未运行" >&2
  exit 1
fi

echo "▶ 测试 1：/health 200 OK"
docker exec rtvoice-tts python -c "
import json, urllib.request
r = json.load(urllib.request.urlopen('http://127.0.0.1:9880/health'))
assert r.get('status') == 'ok'
print('  ✅', r)
"

echo "▶ 测试 2：/voices 含中文音色"
docker exec rtvoice-tts python -c "
import json, urllib.request
r = json.load(urllib.request.urlopen('http://127.0.0.1:9880/voices'))
zh = [v for v in r['voices'] if v.startswith(('zf_','zm_'))]
assert len(zh) >= 1, f'no chinese voices'
print(f'  ✅ {len(r[\"voices\"])} 音色，含 {len(zh)} 个中文：{zh[:3]}')
"

echo "▶ 测试 3：/tts/stream 返回 chunked PCM"
docker exec rtvoice-tts python -c "
import json, time, urllib.request

payload = json.dumps({'text': '你好。', 'voice': 'zf_xiaobei'}).encode()
req = urllib.request.Request('http://127.0.0.1:9880/tts/stream',
    data=payload, headers={'Content-Type': 'application/json'}, method='POST')
t0 = time.time()
resp = urllib.request.urlopen(req, timeout=120)
sr = int(resp.headers.get('X-Sample-Rate'))
fmt = resp.headers.get('X-Format')
total = 0; first = None
while True:
    c = resp.read(8192)
    if not c: break
    if first is None: first = time.time() - t0
    total += len(c)
audio_s = total / 2 / sr
elapsed = time.time() - t0
print(f'  ✅ sr={sr} fmt={fmt} TTFB={first*1000:.0f}ms total={elapsed*1000:.0f}ms audio={audio_s:.2f}s rtf={audio_s/elapsed:.2f}x')
assert sr == 24000
assert total > 0
"

echo "✅ tts-server 集成测试通过"
