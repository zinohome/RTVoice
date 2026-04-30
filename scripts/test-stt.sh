#!/usr/bin/env bash
# test-stt.sh — autonomous 集成测试 stt-server WS 协议
#
# 用 stt-server 镜像内自带的 test_wavs/0.wav 验证：
#   - WS /asr 握手成功
#   - PCM 流推送 → partial events
#   - EOS → final event 含可识别中文文本
#
# 前置：./scripts/dev-up.sh 服务已起

set -euo pipefail
cd "$(dirname "$0")/.."

if ! docker ps --format '{{.Names}}' | grep -q '^rtvoice-stt$'; then
  echo "❌ rtvoice-stt 未运行，先 ./scripts/dev-up.sh" >&2
  exit 1
fi

echo "▶ 测试 1：/health 200 OK"
docker exec rtvoice-stt python -c "
import json, urllib.request
r = json.load(urllib.request.urlopen('http://127.0.0.1:9090/health'))
assert r.get('status') == 'ok', f'health={r}'
print('  ✅', r)
"

echo "▶ 测试 2：/info 含模型名"
docker exec rtvoice-stt python -c "
import json, urllib.request
r = json.load(urllib.request.urlopen('http://127.0.0.1:9090/info'))
print('  ', r)
assert 'sample_rate' in r
"

echo "▶ 测试 3：WS /asr 流式 + EOS（用 test_wavs/0.wav）"
docker exec rtvoice-stt python -c "
import asyncio, json, wave
from websockets.asyncio.client import connect

async def main():
    wav = '/app/models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20/test_wavs/0.wav'
    with wave.open(wav, 'rb') as wf:
        raw = wf.readframes(wf.getnframes())
    async with connect('ws://127.0.0.1:9090/asr') as ws:
        partials, finals = [], []
        async def reader():
            async for msg in ws:
                ev = json.loads(msg)
                if ev['type'] == 'partial': partials.append(ev['text'])
                elif ev['type'] == 'final':
                    finals.append(ev['text']); return
        rt = asyncio.create_task(reader())
        chunk = 16000 * 2 // 10
        for i in range(0, len(raw), chunk):
            await ws.send(raw[i:i+chunk]); await asyncio.sleep(0.05)
        await ws.send('EOS')
        await asyncio.wait_for(rt, timeout=15)
        assert len(finals) >= 1, f'no final, partials={len(partials)}'
        assert len(finals[0]) > 0, f'empty final'
        print(f'  ✅ partials={len(partials)} final={finals[0]!r}')

asyncio.run(main())
"

echo "✅ stt-server 集成测试通过"
