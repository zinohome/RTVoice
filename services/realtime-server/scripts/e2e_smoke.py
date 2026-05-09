"""SP3 prod E2E smoke test.

Self-contained：用 TTS server 合成一句已知中文文本（24k PCM）→ 重采样 16k →
经 realtime-server WS 喂回去 → 验整 turn 链路。

Run inside `rtvoice-realtime` container（依赖 httpx + websockets，已装）：
    docker exec rtvoice-realtime python3 /app/scripts/e2e_smoke.py

或本地：
    python3 services/realtime-server/scripts/e2e_smoke.py \
        --rt http://realtime-server:9000 --tts http://tts-server:9880

退出码：0=全过 / 非 0=有断言失败（哪条会打印 FAIL）
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
import websockets


def resample_24k_to_16k(pcm24: bytes) -> bytes:
    """linear decimation 24k→16k mono int16. 简陋但够用做 STT 测试."""
    import struct
    n = len(pcm24) // 2
    samples = struct.unpack(f"<{n}h", pcm24)
    # 24k → 16k = 取每 3 个采样的 2 个（粗暴 3:2 抽样；语音质量够 STT 识别）
    out = []
    i = 0.0
    step = 24000 / 16000  # 1.5
    while i < n:
        out.append(samples[int(i)])
        i += step
    return struct.pack(f"<{len(out)}h", *out)


async def synth_tts(tts_url: str, text: str) -> bytes:
    """TTS 服务合成 24k mono int16 PCM."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{tts_url}/v1/tts/stream",
            json={"text": text, "voice": "default_zh_female", "lang": "cmn", "speed": 1.0},
        )
        r.raise_for_status()
        return r.content


async def run_one_turn(
    rt_http: str,
    rt_ws: str,
    pcm16k: bytes,
    prompt: str,
    audit: bool,
) -> dict:
    """创 session + WS + 喂 PCM + 收事件，返回 stats."""
    # 1. 创 session
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"{rt_http}/v1/sessions",
            json={"prompt": prompt, "audit_persist": audit},
        )
        r.raise_for_status()
        sess = r.json()
    print(f"[1] session_id={sess['session_id']}  audit={sess['audit_persist']}")

    # 2. WS connect
    ws_url = sess["ws_url"].replace("realtime-server", rt_ws)
    print(f"[2] connect WS {ws_url}")

    events: list[dict] = []
    pcm_bytes_total = 0
    started = time.time()

    async with websockets.connect(ws_url, max_size=None) as ws:
        # 喂 PCM（100ms 块；16k mono int16 → 100ms = 1600 samples = 3200 bytes）
        chunk = 3200
        sent = 0
        while sent < len(pcm16k):
            await ws.send(pcm16k[sent : sent + chunk])
            sent += chunk
            await asyncio.sleep(0.05)  # 半实时（避免 STT 缓冲爆）
        print(f"[3] sent {sent} bytes PCM ({sent/3200*100:.0f}ms 等价时长)")
        await ws.send("audio.eos")
        print("[4] EOS sent，开始收事件")

        # 收 30s 或 response.done 为止
        deadline = time.time() + 30
        try:
            while time.time() < deadline:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                if isinstance(msg, bytes):
                    pcm_bytes_total += len(msg)
                else:
                    ev = json.loads(msg)
                    events.append(ev)
                    et = ev.get("type")
                    if et in ("transcript.final", "response.done"):
                        print(f"    [{et}] {(ev.get('text') or '')[:50]}")
                    elif et == "error":
                        print(f"    [error] {ev.get('code')}: {ev.get('message')}")
                    if et == "response.done":
                        break
        except asyncio.TimeoutError:
            print("[!] 30s 内 WS 静默；可能 STT 没识别出文本")

    elapsed = time.time() - started
    print(f"[5] turn 完成 elapsed={elapsed:.1f}s pcm_out={pcm_bytes_total} events={len(events)}")
    return {
        "session_id": sess["session_id"],
        "events": events,
        "pcm_bytes": pcm_bytes_total,
        "elapsed_s": elapsed,
    }


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--rt", default=os.environ.get("RT_HTTP", "http://realtime-server:9000"))
    p.add_argument("--rt-ws", default=os.environ.get("RT_WS", "realtime-server"),
                   help="WS hostname (替换 ws_url 里的 realtime-server)")
    p.add_argument("--tts", default=os.environ.get("TTS_URL", "http://tts-server:9880"))
    p.add_argument("--audit-dir", default="/data/transcripts",
                   help="audit JSONL 容器内路径（用于校验落盘）")
    p.add_argument("--text", default="今天天气真好")
    p.add_argument("--prompt", default="你是助手，回答用一句话。")
    args = p.parse_args()

    fails = []

    # === Step 1: 准备测试 PCM ===
    print(f"=== synth TTS «{args.text}» @ {args.tts} ===")
    pcm24 = await synth_tts(args.tts, args.text)
    pcm16 = resample_24k_to_16k(pcm24)
    print(f"  24k PCM={len(pcm24)} bytes → 16k PCM={len(pcm16)} bytes\n")

    # === Step 2: turn 1 with audit ===
    print("=== Turn 1: 含 audit_persist=true ===")
    r1 = await run_one_turn(args.rt, args.rt_ws, pcm16, args.prompt, audit=True)
    types = [e.get("type") for e in r1["events"]]
    if "transcript.final" not in types:
        fails.append("Turn1: missing transcript.final")
    if "response.done" not in types:
        fails.append("Turn1: missing response.done")
    rtx_count = sum(1 for t in types if t == "response.text")
    if rtx_count == 0:
        fails.append("Turn1: no response.text events")
    else:
        print(f"  ✓ response.text 事件数: {rtx_count}")
    if r1["pcm_bytes"] == 0:
        fails.append("Turn1: no PCM out from agent")
    else:
        print(f"  ✓ agent 回复 PCM: {r1['pcm_bytes']} bytes")
    final_evt = next((e for e in r1["events"] if e.get("type") == "transcript.final"), None)
    if final_evt and final_evt.get("text"):
        print(f"  ✓ STT recognized: {final_evt['text'][:60]}")
    done_evt = next((e for e in r1["events"] if e.get("type") == "response.done"), None)
    if done_evt and done_evt.get("text"):
        print(f"  ✓ agent text: {done_evt['text'][:60]}")
    else:
        fails.append("Turn1: response.done lacks text field")

    # === Step 3: 验 audit JSONL 落盘 ===
    print("\n=== 验 audit JSONL 落盘 ===")
    await asyncio.sleep(2)  # 给 background writer 时间 flush
    sid = r1["session_id"]
    today = time.strftime("%Y-%m-%d")
    jsonl_path = Path(args.audit_dir) / today / f"{sid}.jsonl"
    if not jsonl_path.is_file():
        fails.append(f"audit JSONL missing: {jsonl_path}")
    else:
        lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
        events = [json.loads(l) for l in lines if l]
        print(f"  ✓ {jsonl_path}: {len(events)} events")
        ev_types = [e.get("event") for e in events]
        for required in ("transcript.final", "response.done"):
            if required not in ev_types:
                fails.append(f"audit missing event: {required}")
        partial_count = sum(1 for e in ev_types if e == "transcript.partial")
        print(f"  ✓ transcript.partial 在 audit 中: {partial_count} 条")

    # === 总结 ===
    print("\n" + "=" * 50)
    if fails:
        print("FAIL:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("✅ ALL E2E checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
