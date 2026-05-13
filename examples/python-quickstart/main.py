"""RTVoice 接入 quickstart — production-grade Python sample。

跑这个示例 = 验证你的 RTVoice prod + API key + TLS 信任链全跑通。
4 路 service 都触：token / TTS / Realtime session create + DELETE。
(STT 用 WS 流式接口，complex 一些；本示例先聚焦 HTTP 路径，STT 见 README。)

跑法：
    cp .env.example .env       # 填 RTVOICE_BASE_URL + RTVOICE_API_KEY + RTVOICE_CA_FILE
    pip install -e ../../clients/python httpx python-dotenv
    python main.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass


BASE = os.environ.get("RTVOICE_BASE_URL", "https://192.168.66.163").rstrip("/")
KEY = os.environ.get("RTVOICE_API_KEY", "")
CA = os.environ.get("RTVOICE_CA_FILE") or False  # False = verify off
ROOM = os.environ.get("DEMO_ROOM", "quickstart-demo")
IDENTITY = os.environ.get("DEMO_IDENTITY", "alice")


def die(msg: str) -> "NoReturn":
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if not KEY or KEY.startswith("REPLACE"):
        die("RTVOICE_API_KEY 未配置；cp .env.example .env 后填进去")

    auth = {"Authorization": f"Bearer {KEY}"}
    print(f"▶ Target: {BASE}   verify={CA if CA else 'OFF (自测模式)'}")

    with httpx.Client(verify=CA, timeout=30.0, headers=auth) as c:
        # ---------- Step 1: /info 探活 ----------
        r = c.get(f"{BASE}/info")
        if r.status_code != 200:
            die(f"/info 失败 HTTP {r.status_code}: {r.text[:200]}")
        info = r.json()
        print(f"✅ /info — {info.get('service')} v{info.get('version')}")

        # ---------- Step 2: token-server /v1/tokens ----------
        r = c.post(f"{BASE}/v1/tokens",
                   json={"room": ROOM, "identity": IDENTITY})
        if r.status_code == 401:
            die("token-server 401 — API key 不对、scope 不含 'tokens'，或 prod 没起")
        if r.status_code == 403:
            die("token-server 403 — key 缺 scope=tokens；admin CLI rotate 加上")
        r.raise_for_status()
        token_body = r.json()
        jwt = token_body["token"]
        print(f"✅ /v1/tokens → JWT (len={len(jwt)}, room={token_body.get('room')})")

        # ---------- Step 3: tts-server /v1/tts/stream ----------
        text = "你好，这是 RTVoice 接入示例。"
        t0 = time.perf_counter()
        with c.stream("POST", f"{BASE}/v1/tts/stream",
                      json={"text": text, "voice": "default_zh_female", "speed": 1.0},
                      timeout=60.0) as r:
            if r.status_code != 200:
                body = r.read().decode("utf-8", errors="replace")[:200]
                die(f"tts 失败 HTTP {r.status_code}: {body}")
            pcm = b""
            for chunk in r.iter_bytes():
                pcm += chunk
        elapsed = time.perf_counter() - t0
        # PCM int16 LE 24kHz mono → 字节数 / 2 / 24000 = 秒
        audio_seconds = len(pcm) / 2 / 24000
        print(f"✅ /v1/tts/stream → {len(pcm)} bytes ({audio_seconds:.2f}s @ 24kHz, "
              f"server time {elapsed:.2f}s)")

        # 保存到 wav 便于试听（手动包 RIFF 头）
        out_wav = Path(__file__).parent / "hello.wav"
        _save_pcm_as_wav(pcm, out_wav, sample_rate=24000)
        print(f"   → saved {out_wav} (24kHz mono, 试试 ffplay / aplay)")

        # ---------- Step 4: realtime-server /v1/sessions ----------
        r = c.post(f"{BASE}/v1/sessions",
                   json={"voice": "default_zh_female", "speed": 1.0})
        if r.status_code != 201:
            die(f"realtime session 失败 HTTP {r.status_code}: {r.text[:200]}")
        sess = r.json()
        sid, ws_url = sess["session_id"], sess["ws_url"]
        print(f"✅ /v1/sessions → {sid}")
        print(f"   ws_url = {ws_url}  (浏览器连 wss:// + ['bearer.<KEY>'] subprotocol)")

        # 立刻 DELETE 释放 quota（不然占着 idle 30s 才回收）
        r = c.delete(f"{BASE}/v1/sessions/{sid}")
        if r.status_code != 204:
            die(f"DELETE session 失败 HTTP {r.status_code}: {r.text[:200]}")
        print(f"✅ DELETE /v1/sessions/{sid} → 204")

    print()
    print("🎉 全 4 service 接通 OK。下一步看 Grafana per-key dashboard:")
    print("   http://192.168.66.163:3000  (admin/admin) → RTVoice — Per-Key Tenant View")


def _save_pcm_as_wav(pcm: bytes, path: Path, *, sample_rate: int) -> None:
    """Wrap raw PCM int16 LE mono in WAV RIFF header so it can be played."""
    import struct
    data_size = len(pcm)
    header = (
        b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH",
                                16,      # chunk size
                                1,       # PCM
                                1,       # mono
                                sample_rate,
                                sample_rate * 2,  # byte rate
                                2,       # block align
                                16)      # bits per sample
        + b"data" + struct.pack("<I", data_size)
    )
    path.write_bytes(header + pcm)


if __name__ == "__main__":
    main()
